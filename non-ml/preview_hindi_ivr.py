"""Preview a Hindi pre-deployment facility verification call without dialing."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))

from facility_tool.hindi_ivr import FacilityCall, SERVICE_LABELS, render_twiml


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--branch', default='उदाहरण अस्पताल, जयपुर शाखा')
    parser.add_argument('--services', nargs='+', choices=tuple(SERVICE_LABELS), default=['dialysis', 'mri'])
    parser.add_argument('--keys', nargs='*', default=[], help='Keypad answers in order. Use - for silence.')
    parser.add_argument('--twiml', action='store_true', help='Print the active prompt as TwiML instead of the transcript.')
    parser.add_argument('--action-url', default='https://example.invalid/ivr/answer')
    parser.add_argument('--recording-status-url', default='https://example.invalid/ivr/recording')
    args = parser.parse_args(argv)

    try:
        call = FacilityCall(args.branch, args.services)
        transcript = [call.prompt.text]
        for key in args.keys:
            if call.phase == 'done':
                parser.error('The call has already ended. Remove the remaining keys.')
            if call.phase == 'record' and key not in ('#', '-'):
                parser.error('Use # or - to simulate the recording finishing.')
            prompt = call.accept_digit(None if key == '-' else key)
            transcript.append(prompt.text)
        if args.twiml:
            print(render_twiml(call.prompt, action_url=args.action_url,
                               recording_status_url=args.recording_status_url))
        else:
            print('\n\n'.join(transcript))
            print()
            report = {'simulation': True, 'purpose': 'predeployment_verification', 'phase': call.phase, **call.report()}
            print(json.dumps(report, indent=2, ensure_ascii=False))
    except ValueError as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
