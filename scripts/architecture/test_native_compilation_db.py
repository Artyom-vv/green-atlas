import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_compilation_db import ROOT, entries


def test_actual_build_sources_have_commands_and_separate_appkit_flags():
    commands = entries(
        (ROOT / "tools/autocad-bridge/build-macos.sh").read_text(),
        ROOT,
        "/compiler",
        "/sdk",
        "arm64",
    )
    native = ROOT / "tools/autocad-bridge/native"
    expected = {
        str(p)
        for p in native.iterdir()
        if p.suffix in {".cpp", ".mm"} and not p.name.startswith("test_")
    }
    assert {c["file"] for c in commands} == expected
    for command in commands:
        args = command["arguments"]
        assert "-bundle" not in args
        assert args.count("-arch") == 1
        assert ("-include" in args) == command["file"].endswith(".cpp")
        assert ("-fobjc-arc" in args) == command["file"].endswith(".mm")
        assert not any("$" in arg for arg in args)


def test_paths_with_spaces_are_single_arguments():
    commands = entries(
        'xcrun clang++ -I "$sdk_root/inc" "$source_root/x.cpp"\n\n',
        Path("/a spaced repo"),
        "/compiler",
        "/a spaced sdk",
        "arm64",
    )
    assert commands[0]["arguments"][4] == "/a spaced sdk"
    assert (
        commands[0]["arguments"][-1]
        == "/a spaced repo/tools/autocad-bridge/native/x.cpp"
    )
