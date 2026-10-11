import os
import unittest
from pathlib import Path

from wine import (
    COMMON_WINETRICKS_VERBS,
    WineError,
    bat_command_arguments,
    parse_winepath_output,
    prefixed_environment,
    quote_cmd_path,
    winetricks_arguments,
)


class EnvironmentTests(unittest.TestCase):
    def test_prefix_override_does_not_change_the_process_environment(self):
        previous = os.environ.pop("WINEPREFIX", None)
        try:
            env = prefixed_environment(Path("/tmp/gvprep-prefix"))
            self.assertEqual(env["WINEPREFIX"], "/tmp/gvprep-prefix")
            self.assertNotIn("WINEPREFIX", os.environ)
        finally:
            if previous is not None:
                os.environ["WINEPREFIX"] = previous

    def test_host_tools_do_not_inherit_bundled_library_paths(self):
        previous = os.environ.get("LD_LIBRARY_PATH")
        os.environ["LD_LIBRARY_PATH"] = "/tmp/bundled-libs"
        try:
            env = prefixed_environment(Path("/tmp/gvprep-prefix"))
            self.assertNotIn("LD_LIBRARY_PATH", env)
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/tmp/bundled-libs")
            self.assertEqual(env["WINEPREFIX"], "/tmp/gvprep-prefix")
        finally:
            if previous is None:
                os.environ.pop("LD_LIBRARY_PATH", None)
            else:
                os.environ["LD_LIBRARY_PATH"] = previous


class WinetricksArgumentTests(unittest.TestCase):
    def test_quiet_arguments_use_only_the_common_verbs(self):
        self.assertEqual(
            winetricks_arguments(COMMON_WINETRICKS_VERBS),
            [
                "-q",
                "d3dx9",
                "d3dx10",
                "d3dx11_43",
                "d3dcompiler_43",
                "d3dcompiler_47",
                "xact",
                "vcrun2008",
                "vcrun2010",
                "vcrun2019",
            ],
        )
        with self.assertRaises(WineError):
            winetricks_arguments(())
        with self.assertRaises(WineError):
            winetricks_arguments(("directx9",))
        with self.assertRaises(WineError):
            winetricks_arguments(("d3dx9", "d3dx9"))


class BatQuotingTests(unittest.TestCase):
    def test_spaces_are_quoted_as_one_cmd_argument(self):
        path = r"Z:\Games\My Game\fix.bat"
        self.assertEqual(quote_cmd_path(path), '"' + path + '"')
        self.assertEqual(
            bat_command_arguments(path),
            ["cmd.exe", "/s", "/c", '"' + path + '"'],
        )

    def test_metacharacters_are_rejected(self):
        for path in (
            r"Z:\Games\a&b.bat",
            r"Z:\Games\100%.bat",
            r"Z:\Games\say!.bat",
            r"Z:\Games\a^b.bat",
            r'Z:\Games\quote".bat',
            r"Z:\Games\a|b.bat",
            r"Z:\Games\(demo).bat",
        ):
            with self.subTest(path=path):
                with self.assertRaises(WineError):
                    bat_command_arguments(path)

    def test_winepath_output_uses_the_windows_path_line(self):
        text = "fixme:dbghelp ignored\nZ:\\fixes\\My Patch\\fix.bat\n"
        self.assertEqual(parse_winepath_output(text), r"Z:\fixes\My Patch\fix.bat")
        with self.assertRaises(WineError):
            parse_winepath_output("winepath failed\n")


if __name__ == "__main__":
    unittest.main()
