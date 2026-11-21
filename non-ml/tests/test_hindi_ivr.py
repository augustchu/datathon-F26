from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import unittest
from xml.etree import ElementTree as ET

from facility_tool.hindi_ivr import FacilityCall, SERVICE_LABELS, render_twiml


class HindiIvrTests(unittest.TestCase):
    def test_wrong_branch_ends_before_collecting_any_service_answers(self):
        branch = "सोनी अस्पताल, जे एल एन मार्ग शाखा"
        call = FacilityCall(branch, ["dialysis", "nicu"])
        self.assertIn("अधिकृत कर्मचारी", call.prompt.text)
        self.assertIn("फ़ाइंड द राइट केयर की स्वचालित फ़ोन कॉल", call.prompt.text)
        self.assertNotIn("शाखा शाखा", call.prompt.text)
        self.assertIn("जे एल एन मार्ग", call.prompt.text)
        self.assertEqual(call.accept_digit("2").kind, "hangup")
        self.assertEqual(call.report(), {
            "branch": branch,
            "branch_status": "wrong_or_unsure",
            "service_answers": {"dialysis": "not_asked", "nicu": "not_asked"},
            "note_requested": False,
            "evidence_kind": "facility_self_report",
        })
        self.assertEqual(call.accept_digit("3").kind, "hangup")

    def test_branch_silence_and_invalid_input_stay_distinct_from_wrong_branch(self):
        call = FacilityCall("सोनी अस्पताल, जयपुर", ["dialysis"])
        self.assertIn("फिर से", call.accept_digit(None).text)
        self.assertEqual(call.phase, "branch")
        self.assertEqual(call.accept_digit("#").kind, "hangup")
        self.assertEqual(call.report()["branch_status"], "unanswered")
        self.assertEqual(call.report()["service_answers"]["dialysis"], "not_asked")

    def test_service_retries_and_silence_never_record_a_negative_answer(self):
        call = FacilityCall("सोनी अस्पताल, जयपुर", ["dialysis", "mri", "nicu"])
        self.assertIn("डायलिसिस", call.accept_digit(1).text)
        self.assertIn("फिर से", call.accept_digit("3").text)
        self.assertIn("एम आर आई", call.accept_digit(None).text)
        self.assertIn("फिर से", call.accept_digit("").text)
        self.assertIn("एन आई सी यू", call.accept_digit("9").text)
        call.accept_digit("0")
        self.assertEqual(call.phase, "notes")
        self.assertEqual(call.report()["service_answers"], {
            "dialysis": "unanswered", "mri": "unanswered", "nicu": "unsure",
        })
        self.assertEqual(call.accept_digit(None).kind, "hangup")
        self.assertFalse(call.report()["note_requested"])

    def test_full_flow_records_notes_only_after_explicit_consent(self):
        call = FacilityCall("सोनी अस्पताल, जयपुर", ["dialysis", "mri"])
        call.accept_digit(1)
        call.accept_digit(1)
        notes = call.accept_digit(2)
        self.assertEqual(call.phase, "notes")
        self.assertEqual(notes.kind, "gather")
        self.assertIn("सहमति", notes.text)
        self.assertIsNone(ET.fromstring(render_twiml(notes, action_url="/ivr")).find("Record"))

        recording = call.accept_digit(3)
        self.assertEqual(call.phase, "record")
        self.assertIn("निजी जानकारी न बताएँ", recording.text)
        self.assertIn("हैश कुंजी", recording.text)
        tree = ET.fromstring(render_twiml(recording, action_url="/ivr", recording_status_url="/recordings"))
        self.assertEqual([node.tag for node in tree], ["Say", "Record"])
        self.assertEqual(tree.find("Record").attrib, {
            "action": "/ivr", "method": "POST", "finishOnKey": "#", "maxLength": "45",
            "timeout": "5", "playBeep": "true", "recordingStatusCallback": "/recordings",
            "recordingStatusCallbackMethod": "POST", "recordingStatusCallbackEvent": "completed absent",
        })
        last = call.accept_digit("#")
        self.assertEqual(call.phase, "done")
        self.assertIsNotNone(ET.fromstring(render_twiml(last, action_url="/ivr")).find("Hangup"))
        self.assertEqual(json.loads(json.dumps(call.report())), {
            "branch": "सोनी अस्पताल, जयपुर",
            "branch_status": "confirmed",
            "service_answers": {"dialysis": "offered", "mri": "not_offered"},
            "note_requested": True,
            "evidence_kind": "facility_self_report",
        })

    def test_hindi_twiml_dtmf_escapes_branch_and_webhook_values(self):
        call = FacilityCall("ए & बी <अस्पताल>", ["dialysis"])
        prompt = call.prompt
        with self.assertRaises(FrozenInstanceError):
            prompt.text = "बदला हुआ"
        xml = ET.fromstring(render_twiml(prompt, action_url="/ivr?phase=branch&attempt=1"))
        self.assertEqual(xml.find("Gather").attrib, {
            "action": "/ivr?phase=branch&attempt=1", "method": "POST",
            "input": "dtmf", "numDigits": "1", "actionOnEmptyResult": "true", "timeout": "7",
        })
        speak = xml.find("Gather/Say")
        self.assertEqual(speak.attrib, {"voice": "Google.hi-IN-Wavenet-A", "language": "hi-IN"})
        self.assertIn("ए & बी <अस्पताल>", speak.text)

    def test_catalog_matches_lookup_and_four_declines_recording(self):
        config = json.loads((Path(__file__).resolve().parents[1] / "config" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(set(config["service_aliases"]), set(SERVICE_LABELS))
        call = FacilityCall("सोनी अस्पताल, जयपुर", ["dialysis", "dialysis"])
        call.accept_digit("1")
        call.accept_digit("1")
        self.assertEqual(call.phase, "notes")
        self.assertEqual(call.accept_digit("4").kind, "hangup")
        self.assertFalse(call.report()["note_requested"])


if __name__ == "__main__":
    unittest.main()
