# cmux Crossterm patch

This directory vendors Crossterm 0.29.0. cmux enables Kitty keyboard protocol
flags 4 and 16, but upstream 0.29.0 discards the reported shifted key,
PC-101-layout key, and associated text while parsing CSI-u events.

The cmux patch adds `EnhancedKeyEvent`, preserves those fields in the Unix
parser, and keeps the original `KeyEvent` identity and modifiers intact. Remove
the patch when [crossterm-rs/crossterm#968](https://github.com/crossterm-rs/crossterm/issues/968)
ships in the Crossterm version used by cmux.

The patch also makes the Unix `mio` event source report a vanished terminal.
Upstream `read_tty_until_idle` (`src/event/source/unix/mio.rs`) discards `Ok(0)`
and every error kind other than `WouldBlock`/`Interrupted`, so a PTY whose peer
is gone stays readable and the read loop retries forever. cmux returns the
`EIO`/`ENXIO` error and an `UnexpectedEof` error instead, which lets the input
thread exit; an `attach` client whose SSH session dropped then reports a host
input failure and exits instead of spinning at 100% CPU. Drop this half when
upstream stops retrying on those errors.
