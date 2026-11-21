# Coordinate and address audit

Notebook 02 audits the published branch point against the address before notebook 06b prepares lookup. It preserves the source address, coordinates and URLs in `location_records`. In `location_entity_review`, `location_routing_eligible`, `location_hold_reason` and `review_flags` identify the branches that need verification. All three recommendation modes and TravelTime use the saved decision.

## When a point is held

- A published state can reject a distant point even for a branch with no PIN. The reference uses exact normalized state labels and distinct, unambiguous India Post PIN-area points. The lower and upper 2% tails are trimmed from the state reference, with a 75 km buffer beyond the remaining bounds and a minimum of 30 distinct points. This is a screening approximation. A point that passes still needs branch verification.
- A published district can reject a point that looks plausible at state scale. The audit uses the structured district or an explicit `Dist.`/`District` address label, matched within the published state. It requires at least six distinct, unambiguous PIN-area points with closely grouped offices. A reference spanning more than 250 km or conflicting district and PIN evidence disables this check. Valid district references get a 75 km buffer.
- A point more than 150 km from a uniquely matched, explicitly published PIN can be held when the PIN matches the published state, its centre lies inside the state's trimmed reference and every usable office point is within 25 km of that centre. An offset over 50 km remains a review flag. That distinction protects facilities such as the Noida example for PIN 201301, where one office is near the facility and the other office coordinates put the calculated centre about 565 km away.
- An identical point attached to branches in at least three recognized states is held. Shared coordinates within one campus can be legitimate and remain a review flag. A contradiction between the state of the branch and that of the source supplying the selected point also puts the branch on hold.
- A cited review in [reviewed_location_holds.json](config/reviewed_location_holds.json) binds to the source record and the rejected point. This works with public snapshots even if the PIN directory is absent. The audit keeps the cited basis next to the rejected value.

## Cases checked against published addresses

| Branch | Evidence and disposition |
| --- | --- |
| Deep Blood Centre, Faridkot | The public licence and hospital agreement put the centre on Kotkapura Road, Faridkot, Punjab 151203. The old point near Delhi is outside Faridkot district's published bounds. Hold the point pending verified site coordinates. [Licence and agreement](https://www.dasmeshinstitutes.com/dirds/wp-content/uploads/sites/7/2025/01/MOU-Blood-centre-2024.pdf) · [District geography](https://faridkot.nic.in/about-district/) |
| Star Hospitals, Banjara Hills | The original point is 267 km from the PIN 500034 reference. The hospital and NABH both publish the Banjara Hills, Hyderabad address. The audit holds the original point. [Branch address](https://starhospitals.in/locations/banjara-hills) · [NABH certificate](https://portal.nabh.co/Documents/AccreditedList/Hospitals/H-2011-0078_5th%20Edition_Scope%20Correction.pdf) |
| Aster MIMS Kannur and Women & Children Hospital, Thycaud | Both source points put Kerala branches near Delhi. Aster identifies the Kannur branch in Chala East and Kerala's health directory identifies the Thycaud branch in Thiruvananthapuram. Both source points are held. [Aster branch address](https://www.asterhospitals.in/hospitals/aster-mims-kannur) · [Kerala health directory](https://dhs.kerala.gov.in/wp-content/uploads/2025/10/WC.pdf) |
| Community Health Centre, Dudhi | The directory's address names Sonbhadra, Uttar Pradesh. The old point is near Delhi and was accepted by the state-level screen alone. The district check puts it on hold. [Uttar Pradesh district facility report](https://nhm.up.gov.in/Uploads/Sonbhadra/2026-27/Sep/2026-Sep-29-dhs-Sonbhadra.pdf) |
| Jeevan Jyoti Clinic & Hospital, Delhi | The saved source lacks a structured state and reports a point more than 1,200 km from the PIN area. The hospital and Delhi's register agree on the Dilshad Garden address. The cited hold names the source point. [Hospital address](https://jeevanjyoti.net/contact-us/) · [Delhi register](https://health.delhi.gov.in/sites/default/files/ser2.pdf) |

## Results on the November 21, 2026 snapshot

The audit checked 41,489 linked facility branches and put 839 additional branches on hold. The newly held branches came from Virtue Foundation (509), eRaktKosh (329) and NABH (1). Within those 839, 496 had a strong PIN-point conflict, 284 a state-point conflict and 246 a district-point conflict. Flag counts overlap. The rules also withheld all 32 branches across seven states sharing one coordinate.

In the same New Delhi city search for a blood bank, eligible branches fell from 241 to 200. Deep Blood Centre had ranked sixth on its erroneous point. After rebuilding the lookup assets, it appeared only in the audit trace, with no rank or distance in all three modes. The five Jaipur blood-bank recommendations and the single Jaipur dialysis recommendation kept their order. These replays used straight-line distance. A mocked routing call verified that held points are excluded from the destinations sent to TravelTime.

## Resolving a hold

Review the location source, exact branch name, full address and evidence for the actual site point. Apply a verified correction to a new input snapshot and retain the original snapshot for traceability. Rerun notebooks 02 and 06b to publish updated flags. The audit can miss errors when address fields are absent, PIN references disagree, or the wrong point is inside the reference area. Postal-office locations are area references and never replace branch points.
