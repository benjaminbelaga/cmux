# cmux Crossterm patches

This directory vendors Crossterm 0.29.0. cmux carries two patches against it.

## Enhanced key events

cmux enables Kitty keyboard protocol flags 4 and 16, but upstream 0.29.0
discards the reported shifted key, PC-101-layout key, and associated text while
parsing CSI-u events.

The cmux patch adds `EnhancedKeyEvent`, preserves those fields in the Unix
parser, and keeps the original `KeyEvent` identity and modifiers intact. Remove
the patch when [crossterm-rs/crossterm#968](https://github.com/crossterm-rs/crossterm/issues/968)
ships in the Crossterm version used by cmux.

## Exit when the terminal is gone

`src/event/source/unix/mio.rs` reads the terminal in a loop while the
descriptor is readable. The upstream loop treated every read result other than
`WouldBlock` and `Interrupted` as "no data right now" and read again. A hung-up
pty does not behave that way: once its peer is gone, every read returns `EIO`
(or a zero-length read) and mio keeps reporting the descriptor as readable. The
loop therefore spun at 100% CPU for as long as the process lived. An orphaned
`cmux-tui attach` client whose SSH session dropped burned about a core each,
for hours, with nothing left to type into.

The cmux patch moves that loop into `pump_tty_reads`, which classifies a read as
data, `WouldBlock`, `Interrupted`, or a terminal that is gone. `EIO`, `ENXIO`,
end of file and every other read error end the loop with `Err`, so the error
travels through `crossterm::event::poll` to the attach input thread and the
client exits non-zero with a one-line diagnostic instead of spinning. Only
`Interrupted` reads again. The loop is driven by a `TtyReadSource` trait so the
regression tests can feed it a scripted reader, including one that returns
`EIO` exactly once and then asserts the loop stopped after a single read.

Remove the patch when crossterm stops retrying a terminal read that did not
return `WouldBlock`.
