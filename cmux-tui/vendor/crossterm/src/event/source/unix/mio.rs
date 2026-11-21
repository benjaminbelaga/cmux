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
                        let Self { tty_fd, tty_buffer, parser, .. } = self;
                        if let Some(event) =
                            read_tty_until_idle(|buffer| tty_fd.read(buffer), tty_buffer, parser)?
                        {
                            return Ok(Some(event));
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

/// Drains the terminal until it would block, returning the first parsed event.
///
/// `read` reports readiness, so it is only called while the descriptor is
/// readable. A terminal whose PTY has disappeared stays readable forever while
/// every read returns `EIO`/`ENXIO` (or `0` for EOF): without treating those as
/// errors this loop spins at full CPU and the caller never regains control, so
/// an attach client whose SSH session dropped burns a core instead of exiting.
fn read_tty_until_idle(
    mut read: impl FnMut(&mut [u8]) -> io::Result<usize>,
    buffer: &mut [u8; TTY_BUFFER_SIZE],
    parser: &mut Parser,
) -> io::Result<Option<InternalEvent>> {
    loop {
        match read(buffer) {
            Ok(0) => return Err(end_of_input()),
            Ok(read_count) => {
                parser.advance(&buffer[..read_count], read_count == TTY_BUFFER_SIZE);
            }
            // No more data to read at the moment. We will receive another event
            // once more data is available to read.
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => return Ok(None),
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        }

        if let Some(event) = parser.next() {
            return Ok(Some(event));
        }
    }
}

/// EOF from the terminal: the reading end is gone, not merely idle.
fn end_of_input() -> io::Error {
    io::Error::new(io::ErrorKind::UnexpectedEof, "terminal input reached end of file")
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
    use std::io;

    use super::{end_of_input, read_tty_until_idle, Parser, TTY_BUFFER_SIZE};

    /// Reproduces a vanished PTY without a real one: the descriptor stays
    /// readable and every read fails with `EIO`.
    #[test]
    fn tty_read_returns_eio_instead_of_spinning() {
        let mut buffer = [0u8; TTY_BUFFER_SIZE];
        let mut parser = Parser::default();
        let mut reads = 0;

        let result = read_tty_until_idle(
            |_| {
                reads += 1;
                assert!(reads < 3, "terminal read was retried after EIO");
                Err(io::Error::from_raw_os_error(5))
            },
            &mut buffer,
            &mut parser,
        );

        assert_eq!(result.unwrap_err().raw_os_error(), Some(5));
        assert_eq!(reads, 1);
    }

    #[test]
    fn tty_read_returns_error_on_end_of_file() {
        let mut buffer = [0u8; TTY_BUFFER_SIZE];
        let mut parser = Parser::default();
        let mut reads = 0;

        let result = read_tty_until_idle(
            |_| {
                reads += 1;
                assert!(reads < 3, "terminal read was retried after EOF");
                Ok(0)
            },
            &mut buffer,
            &mut parser,
        );

        assert_eq!(result.unwrap_err().kind(), end_of_input().kind());
        assert_eq!(reads, 1);
    }

    #[test]
    fn tty_read_stops_at_would_block() {
        let mut buffer = [0u8; TTY_BUFFER_SIZE];
        let mut parser = Parser::default();

        let result = read_tty_until_idle(
            |_| Err(io::Error::from(io::ErrorKind::WouldBlock)),
            &mut buffer,
            &mut parser,
        );

        assert!(matches!(result, Ok(None)));
    }

    #[test]
    fn tty_read_retries_after_interrupt() {
        let mut buffer = [0u8; TTY_BUFFER_SIZE];
        let mut parser = Parser::default();
        let mut reads = 0;

        let result = read_tty_until_idle(
            |_| {
                reads += 1;
                if reads == 1 {
                    return Err(io::Error::from(io::ErrorKind::Interrupted));
                }
                Err(io::Error::from(io::ErrorKind::WouldBlock))
            },
            &mut buffer,
            &mut parser,
        );

        assert!(matches!(result, Ok(None)));
        assert_eq!(reads, 2);
    }
}
