from __future__ import annotations

import unittest


class InputAdapterTests(unittest.TestCase):
    def test_windows_and_posix_sequences_map_to_shared_actions(self) -> None:
        from agent_bridge.tui.input_posix import parse_key
        from agent_bridge.tui.input_windows import parse_windows_key

        self.assertEqual(parse_key("\x1b[A").value, "up")
        self.assertEqual(parse_key("\x03").value, "quit")
        self.assertEqual(parse_key("c").value, "claim")
        self.assertEqual(parse_windows_key("\xe0", "P").value, "down")
        self.assertEqual(parse_windows_key("\r").value, "view")

    def test_posix_restores_terminal_attributes_after_exception(self) -> None:
        from agent_bridge.tui.input_posix import PosixInputAdapter

        class Terminal:
            def isatty(self): return True
            def fileno(self): return 7

        class Termios:
            ECHO = 1; ICANON = 2; TCSANOW = 3
            def __init__(self): self.set_calls = []
            def tcgetattr(self, fd): return [0, 0, 0, 15, 0, 0, 0]
            def tcsetattr(self, fd, when, attrs): self.set_calls.append((fd, when, attrs))

        termios = Termios()
        adapter = PosixInputAdapter(Terminal(), termios_module=termios)
        with self.assertRaises(RuntimeError):
            with adapter:
                raise RuntimeError("boom")
        self.assertEqual(len(termios.set_calls), 2)
        self.assertEqual(termios.set_calls[-1][2][3], 15)

    def test_windows_restores_console_mode_after_exception(self) -> None:
        from agent_bridge.tui.input_windows import WindowsInputAdapter

        class Console:
            def __init__(self): self.mode = 7; self.set_calls = []
            def get_mode(self): return self.mode
            def set_mode(self, mode): self.mode = mode; self.set_calls.append(mode)

        console = Console()
        with self.assertRaises(RuntimeError):
            with WindowsInputAdapter(console=console):
                raise RuntimeError("boom")
        self.assertEqual(console.set_calls[-1], 7)

    def test_posix_search_reader_collects_a_bounded_line_in_raw_mode(self) -> None:
        from agent_bridge.tui.input_posix import PosixInputAdapter

        class Stream:
            def __init__(self): self.values = iter(("r", "e", "v", "\x7f", "w", "\r"))
            def read(self, count): return next(self.values)

        self.assertEqual(PosixInputAdapter(Stream()).read_line("filter: "), "rew")

    def test_windows_reader_waits_for_a_bounded_timeout_when_no_key_is_ready(self) -> None:
        from agent_bridge.tui.input_windows import WindowsInputAdapter

        class Msvcrt:
            def kbhit(self): return False
        waits = []
        adapter = WindowsInputAdapter(msvcrt_module=Msvcrt(), sleep_fn=waits.append)
        self.assertIsNone(adapter.read_key(0.25))
        self.assertEqual(waits, [0.25])

    def test_posix_adapter_consumes_complete_csi_sequences(self) -> None:
        from agent_bridge.tui.input_posix import PosixInputAdapter

        class Stream:
            def __init__(self, text): self.values = iter(text)
            def isatty(self): return True
            def read(self, count): return next(self.values)
        for sequence, expected in (("\x1b[A", "up"), ("\x1b[5~", "previous_page"), ("\x1b[6~", "next_page")):
            with self.subTest(sequence=sequence):
                stream = Stream(sequence)
                self.assertEqual(PosixInputAdapter(stream, select_fn=lambda *args: ([stream], [], [])).read_key(.25).value, expected)


if __name__ == "__main__":
    unittest.main()
