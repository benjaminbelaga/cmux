use std::{collections::VecDeque, io, time::Duration};

use mio::{unix::SourceFd, Events, Interest, Poll, Token};
use signal_hook_mio::v1_0::Signals;

#[cfg(feature = "event-stream")]
use crate::event::sys::Waker;
use crate::event::{
    source::EventSource, sys::unix::parse::parse_event, timeout::PollTimeout, Event, InternalEvent,
};
use crate::terminal::sys::file_descriptor::{tty_fd, FileDesc};

// Tokens to identify file descriptor
const TTY_TOKEN: Token = Token(0);
const SIGNAL_TOKEN: Token = Token(1);
#[cfg(feature = "event-stream")]
const WAKE_TOKEN: Token = Token(2);

// I (@zrzka) wasn't able to read more than 1_022 bytes when testing
// reading on macOS/Linux -> we don't need bigger buffer and 1k of bytes
// is enough.
const TTY_BUFFER_SIZE: usize = 1_024;

pub(crate) struct UnixInternalEventSource {
    poll: Poll,
    events: Events,
    parser: Parser,
    tty_buffer: [u8; TTY_BUFFER_SIZE],
    tty_fd: FileDesc<'static>,
    signals: Signals,
    #[cfg(feature = "event-stream")]
    waker: Waker,
}

impl UnixInternalEventSource {
    pub fn new() -> io::Result<Self> {
        UnixInternalEventSource::from_file_descriptor(tty_fd()?)
    }

    pub(crate) fn from_file_descriptor(input_fd: FileDesc<'static>) -> io::Result<Self> {
        let poll = Poll::new()?;
        let registry = poll.registry();

        let tty_raw_fd = input_fd.raw_fd();
        let mut tty_ev = SourceFd(&tty_raw_fd);
        registry.register(&mut tty_ev, TTY_TOKEN, Interest::READABLE)?;

        let mut signals = Signals::new([signal_hook::consts::SIGWINCH])?;
        registry.register(&mut signals, SIGNAL_TOKEN, Interest::READABLE)?;

        #[cfg(feature = "event-stream")]
        let waker = Waker::new(registry, WAKE_TOKEN)?;

        Ok(UnixInternalEventSource {
            poll,
            events: Events::with_capacity(3),
            parser: Parser::default(),
            tty_buffer: [0u8; TTY_BUFFER_SIZE],
            tty_fd: input_fd,
            signals,
            #[cfg(feature = "event-stream")]
            waker,
        })
    }
}

/// Reads the bytes the terminal has ready right now. The real implementation
/// wraps the terminal descriptor; tests drive the read loop with a fake one,
/// because a deleted pty cannot be produced on demand.
trait TtyReadSource {
    fn read_tty(&self, buffer: &mut [u8]) -> io::Result<usize>;
}

impl TtyReadSource for FileDesc<'_> {
    fn read_tty(&self, buffer: &mut [u8]) -> io::Result<usize> {
        self.read(buffer)
    }
}

/// Why one pass of terminal reads ended.
#[derive(Debug)]
enum TtyPump {
    /// The parser produced an event.
    Event(InternalEvent),
    /// Nothing more to read right now; wait for the next readiness event.
    Idle,
}

/// Feeds the parser with everything the terminal has ready right now.
///
/// Stops at the first parsed event, at `WouldBlock`, and at a terminal that is
/// gone. It never re-reads a terminal that cannot produce input: a hung-up pty
/// answers every read with `EIO` (or a zero-length read) while mio keeps
/// reporting it as readable, so retrying spins the caller at 100% CPU for as
/// long as the process lives instead of letting it exit. `EIO`, `ENXIO` and end
/// of file are therefore returned as errors, and `Interrupted` is the only
/// result that reads again.
fn pump_tty_reads(
    tty: &impl TtyReadSource,
    parser: &mut Parser,
    buffer: &mut [u8],
) -> io::Result<TtyPump> {
    loop {
        match tty.read_tty(buffer) {
            Ok(0) => {
                return Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "terminal input reached end of file, the terminal is gone",
                ))
            }
            Ok(read_count) => parser.advance(&buffer[..read_count], read_count == buffer.len()),
            Err(e) => match e.kind() {
                // No more data to read at the moment. We will receive another event
                // once more data is available to read.
                io::ErrorKind::WouldBlock => return Ok(TtyPump::Idle),
                io::ErrorKind::Interrupted => continue,
                // Terminal hang-up: the descriptor stays readable forever, so
                // reading it again is a busy loop.
                _ => return Err(e),
            },
        }

        if let Some(event) = parser.next() {
            return Ok(TtyPump::Event(event));
        }
    }
}

impl EventSource for UnixInternalEventSource {
    fn try_read(&mut self, timeout: Option<Duration>) -> io::Result<Option<InternalEvent>> {
        if let Some(event) = self.parser.next() {
            return Ok(Some(event));
        }

        let timeout = PollTimeout::new(timeout);

        loop {
            if let Err(e) = self.poll.poll(&mut self.events, timeout.leftover()) {
                // Mio will throw an interrupted error in case of cursor position retrieval. We need to retry until it succeeds.
                // Previous versions of Mio (< 0.7) would automatically retry the poll call if it was interrupted (if EINTR was returned).
                // https://docs.rs/mio/0.7.0/mio/struct.Poll.html#notes
                if e.kind() == io::ErrorKind::Interrupted {
                    continue;
                } else {
                    return Err(e);
                }
            };

            if self.events.is_empty() {
                // No readiness events = timeout
                return Ok(None);
            }

            for token in self.events.iter().map(|x| x.token()) {
                match token {
                    TTY_TOKEN => {
                        match pump_tty_reads(&self.tty_fd, &mut self.parser, &mut self.tty_buffer)?
                        {
                            TtyPump::Event(event) => return Ok(Some(event)),
                            TtyPump::Idle => {}
                        }
                    }
                    SIGNAL_TOKEN => {
                        if self.signals.pending().next() == Some(signal_hook::consts::SIGWINCH) {
                            // TODO Should we remove tput?
                            //
                            // This can take a really long time, because terminal::size can
                            // launch new process (tput) and then it parses its output. It's
                            // not a really long time from the absolute time point of view, but
                            // it's a really long time from the mio, async-std/tokio executor, ...
                            // point of view.
                            let new_size = crate::terminal::size()?;
                            return Ok(Some(InternalEvent::Event(Event::Resize(
                                new_size.0, new_size.1,
                            ))));
                        }
                    }
                    #[cfg(feature = "event-stream")]
                    WAKE_TOKEN => {
                        return Err(std::io::Error::new(
                            std::io::ErrorKind::Interrupted,
                            "Poll operation was woken up by `Waker::wake`",
                        ));
                    }
                    _ => unreachable!("Synchronize Evented handle registration & token handling"),
                }
            }

            // Processing above can take some time, check if timeout expired
            if timeout.elapsed() {
                return Ok(None);
            }
        }
    }

    #[cfg(feature = "event-stream")]
    fn waker(&self) -> Waker {
        self.waker.clone()
    }
}

//
// Following `Parser` structure exists for two reasons:
//
//  * mimic anes Parser interface
//  * move the advancing, parsing, ... stuff out of the `try_read` method
//
#[derive(Debug)]
struct Parser {
    buffer: Vec<u8>,
    internal_events: VecDeque<InternalEvent>,
}

impl Default for Parser {
    fn default() -> Self {
        Parser {
            // This buffer is used for -> 1 <- ANSI escape sequence. Are we
            // aware of any ANSI escape sequence that is bigger? Can we make
            // it smaller?
            //
            // Probably not worth spending more time on this as "there's a plan"
            // to use the anes crate parser.
            buffer: Vec::with_capacity(256),
            // TTY_BUFFER_SIZE is 1_024 bytes. How many ANSI escape sequences can
            // fit? What is an average sequence length? Let's guess here
            // and say that the average ANSI escape sequence length is 8 bytes. Thus
            // the buffer size should be 1024/8=128 to avoid additional allocations
            // when processing large amounts of data.
            //
            // There's no need to make it bigger, because when you look at the `try_read`
            // method implementation, all events are consumed before the next TTY_BUFFER
            // is processed -> events pushed.
            internal_events: VecDeque::with_capacity(128),
        }
    }
}

impl Parser {
    fn advance(&mut self, buffer: &[u8], more: bool) {
        for (idx, byte) in buffer.iter().enumerate() {
            let more = idx + 1 < buffer.len() || more;

            self.buffer.push(*byte);

            match parse_event(&self.buffer, more) {
                Ok(Some(ie)) => {
                    self.internal_events.push_back(ie);
                    self.buffer.clear();
                }
                Ok(None) => {
                    // Event can't be parsed, because we don't have enough bytes for
                    // the current sequence. Keep the buffer and process next bytes.
                }
                Err(_) => {
                    // Event can't be parsed (not enough parameters, parameter is not a number, ...).
                    // Clear the buffer and continue with another sequence.
                    self.buffer.clear();
                }
            }
        }
    }
}

impl Iterator for Parser {
    type Item = InternalEvent;

    fn next(&mut self) -> Option<Self::Item> {
        self.internal_events.pop_front()
    }
}

#[cfg(test)]
mod tests {
    use std::{
        cell::Cell,
        io,
        io::ErrorKind,
        sync::atomic::{AtomicUsize, Ordering},
    };

    use super::{pump_tty_reads, Parser, TtyPump};

    /// The kernel answer for every read of a hung-up pty. It is not
    /// `WouldBlock`, and mio keeps the descriptor readable, so the read loop
    /// used to come back here forever.
    fn deleted_pty_error() -> io::Error {
        io::Error::from_raw_os_error(5)
    }

    /// Replays a scripted sequence of read results and counts how many times
    /// the loop read, which is what "not spin" means.
    struct ScriptedTty {
        results: std::cell::RefCell<std::vec::IntoIter<io::Result<usize>>>,
        reads: Cell<usize>,
    }

    impl ScriptedTty {
        fn new(results: Vec<io::Result<usize>>) -> Self {
            ScriptedTty {
                results: std::cell::RefCell::new(results.into_iter()),
                reads: Cell::new(0),
            }
        }
    }

    impl super::TtyReadSource for ScriptedTty {
        fn read_tty(&self, _buffer: &mut [u8]) -> io::Result<usize> {
            self.reads.set(self.reads.get() + 1);
            match self.results.borrow_mut().next() {
                Some(result) => result,
                // A terminal that keeps answering `WouldBlock` is the end of
                // every scripted scenario, so a loop that reads past its
                // script is reported as idle rather than panicking.
                None => Err(io::Error::from(ErrorKind::WouldBlock)),
            }
        }
    }

    fn pump(tty: &ScriptedTty) -> (io::Result<TtyPump>, usize) {
        let mut parser = Parser::default();
        let mut buffer = [0u8; super::TTY_BUFFER_SIZE];
        let result = pump_tty_reads(tty, &mut parser, &mut buffer);
        (result, tty.reads.get())
    }

    #[test]
    fn would_block_waits_for_the_next_readiness_event_without_reading_again() {
        let tty = ScriptedTty::new(vec![Err(io::Error::from(ErrorKind::WouldBlock))]);
        let (result, reads) = pump(&tty);
        assert!(matches!(result, Ok(TtyPump::Idle)), "{result:?}");
        assert_eq!(reads, 1, "`WouldBlock` must not read again");
    }

    #[test]
    fn interrupted_reads_again() {
        let tty = ScriptedTty::new(vec![
            Err(io::Error::from(ErrorKind::Interrupted)),
            Err(io::Error::from(ErrorKind::WouldBlock)),
        ]);
        let (result, reads) = pump(&tty);
        assert!(matches!(result, Ok(TtyPump::Idle)), "{result:?}");
        assert_eq!(reads, 2, "`Interrupted` must retry exactly once");
    }

    /// The regression: a client whose terminal disappeared used to re-read
    /// forever, one `EIO` per loop iteration. That is how an orphaned
    /// `cmux-tui attach` client burned about 90% of a core for a day. One
    /// `EIO` must end the loop with an error.
    #[test]
    fn eio_from_a_deleted_terminal_ends_the_read_loop() {
        let tty = ScriptedTty::new(vec![Err(deleted_pty_error())]);
        let (result, reads) = pump(&tty);
        let error = result.expect_err("`EIO` must end the read loop with an error");
        assert_eq!(error.raw_os_error(), Some(5), "{error:?}");
        assert_eq!(reads, 1, "`EIO` must not read again");
    }

    /// A hang-up also shows up as a zero-length read, which is neither
    /// `WouldBlock` nor an error and must end the loop too.
    #[test]
    fn end_of_file_ends_the_read_loop() {
        let tty = ScriptedTty::new(vec![Ok(0)]);
        let (result, reads) = pump(&tty);
        let error = result.expect_err("end of file must end the read loop with an error");
        assert_eq!(error.kind(), ErrorKind::UnexpectedEof, "{error:?}");
        assert_eq!(reads, 1, "end of file must not read again");
    }

    /// Any other terminal read failure means the same thing: the terminal is
    /// unusable, so the loop must not keep reading it.
    #[test]
    fn other_read_errors_end_the_read_loop() {
        for kind in [ErrorKind::BrokenPipe, ErrorKind::ConnectionReset, ErrorKind::NotConnected] {
            let tty = ScriptedTty::new(vec![Err(io::Error::new(kind, "terminal hangup"))]);
            let (result, reads) = pump(&tty);
            assert_eq!(
                result.expect_err("`{kind:?}` must end the read loop").kind(),
                kind,
                "`{kind:?}` must be surfaced unchanged"
            );
            assert_eq!(reads, 1, "`{kind:?}` must not read again");
        }
    }

    #[test]
    fn bytes_read_reach_the_parser_and_stop_the_loop() {
        /// Answers one real byte, then idles. The scripted reader cannot fill
        /// the buffer, so the byte has to be planted by the reader itself.
        struct ByteThenIdle {
            pending_byte: std::cell::Cell<bool>,
            reads: AtomicUsize,
        }

        impl super::TtyReadSource for ByteThenIdle {
            fn read_tty(&self, buffer: &mut [u8]) -> io::Result<usize> {
                self.reads.fetch_add(1, Ordering::SeqCst);
                if self.pending_byte.replace(false) {
                    buffer[0] = b'a';
                    Ok(1)
                } else {
                    Err(io::Error::from(ErrorKind::WouldBlock))
                }
            }
        }

        let tty =
            ByteThenIdle { pending_byte: std::cell::Cell::new(true), reads: AtomicUsize::new(0) };
        let mut parser = Parser::default();
        let mut buffer = [0u8; super::TTY_BUFFER_SIZE];
        let result = pump_tty_reads(&tty, &mut parser, &mut buffer);
        assert!(matches!(result, Ok(TtyPump::Event(_))), "{result:?}");
        assert_eq!(tty.reads.load(Ordering::SeqCst), 1, "the event stops the loop");
    }
}
