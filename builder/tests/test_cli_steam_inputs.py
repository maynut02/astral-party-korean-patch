import json
from contextlib import nullcontext
from dataclasses import dataclass
from uuid import UUID

import astral_builder.cli as cli

REVISION_ID = "00000000-0000-0000-0000-000000000012"


def test_steam_input_command_writes_catalog_plan(tmp_path, monkeypatch, capsys):
    @dataclass
    class Plan:
        route: str = "CN_STEAM"
        filelist: tuple[str, ...] = ("game/data.unity3d", "game/StreamingAssets/aa/font.bundle")

    connection = object()
    captured = {}
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(cli.psycopg, "connect", lambda _url: nullcontext(connection))

    def plan(conn, **kwargs):
        assert conn is connection
        captured.update(kwargs)
        return Plan()

    monkeypatch.setattr(cli, "plan_steam_inputs", plan)
    output = tmp_path / "plans" / "cn_steam.json"
    filelist = tmp_path / "cn_steam.files"
    assert (
        cli.main(
            [
                "steam-inputs",
                "--revision-id",
                REVISION_ID,
                "--route-config",
                "routes/cn_steam.yaml",
                "--filelist",
                str(filelist),
                "--plan-output",
                str(output),
            ]
        )
        == 0
    )
    assert captured["revision_id"] == UUID(REVISION_ID)
    assert captured["filelist_path"] == str(filelist)
    assert captured["route_config"] == "routes/cn_steam.yaml"
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved == json.loads(capsys.readouterr().out)
    assert saved["filelist"][-1].endswith("StreamingAssets/aa/font.bundle")


def test_build_parser_accepts_explicit_steam_data_directory():
    args = cli.build_parser().parse_args(
        [
            "build",
            "--revision-id",
            REVISION_ID,
            "--route-config",
            "routes/int_steam.yaml",
            "--asset-base-url",
            "https://example.test/release",
            "--source-asset-base-url",
            "https://example.test/original",
            "--patch-version",
            "v3.3.0_r012_p0",
            "--steam-data-dir",
            "inputs/INT_Data",
        ]
    )
    assert args.steam_data_dir == "inputs/INT_Data"
