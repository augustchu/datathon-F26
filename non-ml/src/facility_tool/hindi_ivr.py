"""Hindi keypad prompts for facility self-reports and optional voice notes."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from xml.etree import ElementTree as ET


SERVICE_LABELS = {
    "dialysis": "डायलिसिस",
    "mri": "एम आर आई जाँच",
    "nicu": "नवजात शिशु गहन देखभाल इकाई, यानी एन आई सी यू",
    "nicu_level_3": "स्तर तीन की नवजात शिशु गहन देखभाल इकाई, यानी लेवल थ्री एन आई सी यू",
    "emergency_c_section": "आपातकालीन सीज़ेरियन प्रसव, यानी इमरजेंसी सी सेक्शन",
    "cardiac_surgery": "हृदय की सर्जरी",
    "burn_care": "जलने की चोटों का इलाज",
    "trauma_care": "गंभीर चोटों की आपात देखभाल, यानी ट्रॉमा केयर",
    "blood_bank": "ब्लड बैंक",
    "blood_storage_unit": "रक्त भंडारण इकाई, यानी ब्लड स्टोरेज यूनिट",
    "audio_verbal_therapy": "सुनने और बोलने का प्रशिक्षण, यानी ऑडिटरी वर्बल थेरेपी",
}
VOICE = "Google.hi-IN-Wavenet-A"
LANGUAGE = "hi-IN"
_RETRY = "यह विकल्प समझ में नहीं आया। कृपया फिर से कोशिश करें। "
_NOTES = (
    "अगर आप सूची में छूटी हुई सेवाओं के बारे में अपना संदेश रिकॉर्ड कराने की सहमति देते हैं, तो तीन दबाएँ। "
    "बिना रिकॉर्डिंग कॉल समाप्त करने के लिए चार दबाएँ।"
)
_RECORD = (
    "बीप के बाद सिर्फ़ छूटी हुई सेवाओं के नाम बताएँ। "
    "किसी मरीज़ का नाम, फ़ोन नंबर या कोई निजी जानकारी न बताएँ। "
    "संदेश पूरा होने पर हैश कुंजी दबाएँ।"
)
_GOODBYE = "आपके समय के लिए धन्यवाद।"
_SERVICE_ANSWERS = {"1": "offered", "2": "not_offered", "0": "unsure"}


@dataclass(frozen=True, slots=True)
class Prompt:
    kind: Literal["gather", "record", "hangup"]
    text: str


class FacilityCall:
    """Track one call and return facility self-reports without external writes."""

    def __init__(self, branch: str, services: Iterable[str]):
        if not isinstance(branch, str) or not branch.strip():
            raise ValueError("Provide the exact facility branch name.")
        if isinstance(services, str):
            raise ValueError("Provide service keys as a sequence.")
        requested = tuple(dict.fromkeys(services))
        if not requested or any(service not in SERVICE_LABELS for service in requested):
            raise ValueError("Provide one or more service keys from the Hindi catalogue.")
        self._branch = " ".join(branch.split())
        self._services = requested
        self._answers = dict.fromkeys(requested, "not_asked")
        self._branch_status = "pending"
        self._note_requested = False
        self._phase = "branch"
        self._retry = False
        self._service_index = 0

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def prompt(self) -> Prompt:
        prefix = _RETRY if self._retry else ""
        if self._phase == "branch":
            return Prompt(
                "gather",
                prefix
                + "नमस्कार। यह फ़ाइंड द राइट केयर की स्वचालित फ़ोन कॉल है। "
                + "हम स्वास्थ्य सेवाओं की सूची की पुष्टि कर रहे हैं ताकि मरीज़ ज़रूरी इलाज देने वाली जगह ढूँढ सकें। "
                + f"क्या आप {self._branch} के अधिकृत कर्मचारी हैं और इस शाखा की सेवाओं की जानकारी दे सकते हैं? "
                + "अगर हाँ, तो एक दबाएँ। अगर यह दूसरी शाखा है या आपको जानकारी नहीं है, तो दो दबाएँ।",
            )
        if self._phase == "service":
            service = SERVICE_LABELS[self._services[self._service_index]]
            return Prompt(
                "gather",
                prefix
                + f"क्या इस शाखा में {service} की सुविधा दी जाती है? "
                + "अगर हाँ, तो एक दबाएँ। अगर नहीं, तो दो दबाएँ। अगर पता नहीं है, तो शून्य दबाएँ।",
            )
        if self._phase == "notes":
            return Prompt("gather", prefix + _NOTES)
        if self._phase == "record":
            return Prompt("record", _RECORD)
        return Prompt("hangup", _GOODBYE)

    def _move(self, phase: str) -> None:
        self._phase = phase
        self._retry = False

    def _advance_service(self, answer: str) -> None:
        self._answers[self._services[self._service_index]] = answer
        self._service_index += 1
        self._move("notes" if self._service_index == len(self._services) else "service")

    def accept_digit(self, digit: str | int | None) -> Prompt:
        """Accept one keypad digit or empty input and return the next prompt."""
        digit = "" if digit is None else str(digit).strip()
        if self._phase == "branch":
            if digit == "1":
                self._branch_status = "confirmed"
                self._move("service")
            elif digit == "2" or self._retry:
                self._branch_status = "wrong_or_unsure" if digit == "2" else "unanswered"
                self._move("done")
            else:
                self._retry = True
        elif self._phase == "service":
            if digit in _SERVICE_ANSWERS:
                self._advance_service(_SERVICE_ANSWERS[digit])
            elif self._retry:
                self._advance_service("unanswered")
            else:
                self._retry = True
        elif self._phase == "notes":
            if digit == "3":
                self._note_requested = True
                self._move("record")
            elif digit in ("", "4") or self._retry:
                self._move("done")
            else:
                self._retry = True
        elif self._phase == "record":
            self._move("done")
        return self.prompt

    def report(self) -> dict:
        """Return a JSONable snapshot of the answers collected so far."""
        return {
            "branch": self._branch,
            "branch_status": self._branch_status,
            "service_answers": dict(self._answers),
            "note_requested": self._note_requested,
            "evidence_kind": "facility_self_report",
        }


def _say(parent, text: str) -> None:
    ET.SubElement(parent, "Say", {"voice": VOICE, "language": LANGUAGE}).text = text


def render_twiml(prompt: Prompt, *, action_url: str, recording_status_url: str | None = None) -> str:
    """Render a prompt for Twilio without placing a call or enabling transcription."""
    response = ET.Element("Response")
    if prompt.kind == "hangup":
        _say(response, prompt.text)
        ET.SubElement(response, "Hangup")
    elif prompt.kind in ("gather", "record"):
        if not isinstance(action_url, str) or not action_url.strip():
            raise ValueError("Provide the call action URL.")
        action = {"action": action_url, "method": "POST"}
        if prompt.kind == "gather":
            gather = ET.SubElement(response, "Gather", {
                **action, "input": "dtmf", "numDigits": "1", "actionOnEmptyResult": "true", "timeout": "7",
            })
            _say(gather, prompt.text)
        else:
            _say(response, prompt.text)
            attributes = {**action, "finishOnKey": "#", "maxLength": "45", "timeout": "5", "playBeep": "true"}
            if recording_status_url:
                attributes.update(
                    recordingStatusCallback=recording_status_url,
                    recordingStatusCallbackMethod="POST",
                    recordingStatusCallbackEvent="completed absent",
                )
            ET.SubElement(response, "Record", attributes)
    else:
        raise ValueError("Unknown IVR prompt type.")
    return ET.tostring(response, encoding="unicode")
