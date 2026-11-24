import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location("isaac_tools_cli",Path(__file__).resolve().parents[1]/"skills/isaac-play/scripts/isaac_tools.py")
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        def request(path,payload=None):
            if path=="/tools":return {"ok":True,"tools":[{"name":"fixture","inputSchema":{"properties":{
                "decision_id":{"type":"string"},"event_cursor":{"type":"integer"},"timeout_s":{"type":"number"},
                "reason":{"type":"string"},"enabled":{"type":"boolean"},"names":{"type":"array","items":{"type":"string"}},"entry":{"type":"object"}}}}]}
            self.calls.append((path,payload));return {"ok":True,"result":payload["arguments"]}
        self.patch=patch.object(cli,"request",request);self.patch.start();self.addCleanup(self.patch.stop)

    def invoke(self,argv):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):cli.main(argv)
        return json.loads(out.getvalue())

    def test_simple_arguments_keep_string_identity_and_cast_numeric_parameters(self):
        reply=self.invoke(["call","fixture","--arg","decision_id","123","--arg","event_cursor","7",
            "--arg","timeout_s","2.5","--arg","reason","绕障测试 true","--arg","enabled","false"])
        self.assertEqual(reply["result"],{"decision_id":"123","event_cursor":7,"timeout_s":2.5,"reason":"绕障测试 true","enabled":False})
        self.assertEqual(len(self.calls),1)

    def test_repeated_string_array_entries_are_supported(self):
        reply=self.invoke(["call","fixture","--arg","names","Sad Onion","--arg","names","伤心洋葱"])
        self.assertEqual(reply["result"]["names"],["Sad Onion","伤心洋葱"])

    def test_bad_duplicate_or_unknown_arguments_never_submit_tool_calls(self):
        for pairs in (["--arg","event_cursor","3.5"],["--arg","timeout_s","nan"],["--arg","enabled","1"],
            ["--arg","cursor","3"],["--arg","reason","a","--arg","reason","b"],["--arg","entry","{}"]):
            with self.subTest(pairs=pairs),contextlib.redirect_stdout(io.StringIO()),self.assertRaises(SystemExit):
                cli.main(["call","fixture",*pairs])
        self.assertFalse(self.calls)

    def test_legacy_json_and_bom_files_keep_nested_data(self):
        self.assertEqual(self.invoke(["call","fixture","--json",'{"entry":{"x":1}}'])["result"],{"entry":{"x":1}})
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"args.json";path.write_text(json.dumps({"entry":{"summary":"中文"}},ensure_ascii=False),encoding="utf-8-sig")
            self.assertEqual(self.invoke(["call","fixture","--args-file",str(path)])["result"],{"entry":{"summary":"中文"}})

    def test_argument_sources_cannot_silently_override_one_another(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            cli.main(["call","fixture","--json","{}","--arg","reason","test"])
        self.assertFalse(self.calls)

    def test_check_identifies_stalled_alive_game_without_controlling_it(self):
        state={"connected":True,"run_id":"seed:1","run_instance":1,"room_id":"1:0:84","game_frame":1957,
            "last_frame_age_ms":18000,"player":{"dead":False},"raw_frame":{"mode":"manual"},"autoplay":False}
        with patch.object(cli,"call",return_value=state) as call,patch.object(cli.time,"sleep"):
            result=cli.check_game(2)
        self.assertFalse(result["ready_for_start"]);self.assertFalse(result["frames_advancing"]);self.assertTrue(result["alive"])
        self.assertEqual(call.call_args_list[0].args,("observe_game",{"detail":"full"}))
        self.assertTrue(all(c.args[0]=="observe_game" for c in call.call_args_list))

    def test_check_requires_same_run_advancing_manual_alive_game(self):
        first={"connected":True,"run_id":"seed:1","run_instance":1,"game_frame":10,"last_frame_age_ms":10,
            "player":{"dead":False},"raw_frame":{"mode":"manual"},"autoplay":False}
        for changes,ready in [({"game_frame":70},True),({"game_frame":70,"run_instance":2},False),
            ({"game_frame":70,"raw_frame":{"mode":"ai"}},False),({"game_frame":70,"player":{"dead":True}},False),
            ({"game_frame":70,"autoplay":True},False)]:
            with self.subTest(changes=changes),patch.object(cli,"call",side_effect=[first,{**first,**changes}]),patch.object(cli.time,"sleep"):
                self.assertEqual(cli.check_game(2)["ready_for_start"],ready)
