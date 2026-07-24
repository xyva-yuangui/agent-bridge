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


if __name__ == "__main__":
    unittest.main()
