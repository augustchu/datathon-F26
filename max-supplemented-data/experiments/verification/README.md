# In The Loop verification sample

**Human review pending.** The screenshot and receipt below show the smaller desk-review pilot. The independent human audit has its own sample and review list.

## Scraper fidelity

The [scraper precision and recall audit](scraper-audit/README.md) checks whether the archived IFFCO and SBI disclosures were captured faithfully. The precision draw starts from emitted bed and doctor values. The recall draw starts independently from the original disclosures. The [In The Loop accuracy test](../../../In%20The%20Loop%20Verification%20Tool/README.md#test-scraper-accuracy-with-a-random-sample) selects the minimum random output-first sample under the reviewer's specified threshold, significance level and power.

## Size of the human audit

The pilot used a fixed quota of three new branches from each of three source groups, plus one earlier reference case. The 20 rows were candidate claims for 10 branches. Multiple claims at one branch share the same facts and count as one branch check.

The new audit frame contains **195 branches with competing numeric bed counts** after removing earlier training cases, direct reviews, and identity or unresolved-count holds. We built that frame without reading the desk-review decisions. For an overall branch-level rate at **95% confidence with an approximate ±5 percentage-point margin**, use a simple random sample without replacement and allow for the worst-case rate of 50%. The finite-population calculation gives **130 branches**. Fifty branches would give approximately **±12 percentage points** under the same assumptions. The original per-source scraper fidelity audit is a separate question. Accuracy within each insurer would require its own sample design and enough conclusive evidence for that insurer.

The worklist has one pending row per sampled branch with the competing counts, dates and available original links. The previously set 50-case threshold makes this a list-only task. `evidence_source_url` and `full_page_screenshot_path` are blank until a reviewer collects evidence. If the branch remains inconclusive, count it when reporting the review yield and report the unresolved share. The ±5 target applies to an observable rate, such as the share of sampled branches with an exact count supported by the protocol. Precision for underlying source accuracy also depends on how many claims can actually be resolved.

The same run generates `publisher-links-review-queue.csv` for the [link-only In The Loop version](../../../In%20The%20Loop%20Verification%20Tool/README.md). It has 161 linked publisher claims across the same 130 branches. The existing IFFCO links highlight the facility name and the SBI links target the cited PDF page. Among the linked insurer claims, 21 branches have different counts, nine agree and 100 have a single insurer. Choose **Differences only** to see the 21 disputes. The shared `case_id` connects the claims to the branch worklist. Confirming that a page contains a count checks what the publisher reported. The branch worklist records the independent source used to adjudicate conflicting counts.

```sh
python max-supplemented-data/experiments/verification/build_human_worklist.py \
  --local max-supplemented-data/local \
  --output max-supplemented-data/local/in-the-loop-human-sample-replay
```

## Earlier desk-review pilot

On November 21, 2026, Codex reviewed public branch evidence for nine prospectively sampled disputes and revisited one earlier reference case. The review found **two exact whole-branch bed counts** and **eight inconclusive cases**. The two exact counts are assertions from the facility operators with unspecified observation dates. Scoring measures agreement with those assertions. [Branch-level decisions and source links](../source_selection/evidence.json)

The nine disputes were frozen by branch hash, with three from each of the IFFCO-only, SBI-only and differing dual-insurer groups. We checked the branch name and PIN, the count definition and the observation period wherever available. A sanctioned count, a minimum, a ward count or an earlier count can support a narrower claim. An exact whole-branch count needs its own evidence. This targeted sample measures how often the sampled disputes could be resolved under that definition. It has limited support for comparing publisher accuracy.

The blinded CSV created **20 candidate rows** for the ten cases, with screenshots and no preloaded decisions. Codex read the source pages and recorded the findings through the app, then exported the desk-review CSV. The app marked **2 accepted, 2 rejected and 16 inconclusive**. Rejected here means the count differs from the operator's published assertion at review. The older candidate may have been accurate for its original reporting period. The desk-review export contains candidate values and stays with the authorized local run. [Desk-review screenshot](screenshots/in-the-loop-desk-review.png) · [Desk-review receipt](result.json)

## Historical pilot captures

These earlier captures show the relevant page area. Use a full-page image and its original evidence URL for each new review item. The original URL identifies the evidence page. Keep a candidate claim's separate publisher URL as well when available.

| Case | Branch | Visible evidence in the capture |
| --- | --- | --- |
| C01 | Girdhar Hospital, Sonipat | Three beds in a general ward. [Screenshot](screenshots/C01.png) |
| C02 | Usha Hospital, Muzaffarpur | Operator's branch address on Juran Chhapra Road. [Screenshot](screenshots/C02.png) |
| C03 | BHS Lakeview Hospital, Belagavi | District directory verifies the Fort Lake branch address. [Screenshot](screenshots/C03.png) |
| C04 | Bhagwan Das Hospital, Sonipat | Operator's facility description includes a lower bound. [Screenshot](screenshots/C04.png) |
| C05 | Noble Heart Hospital, Rohtak | Operator identifies its Rohtak hospital. [Screenshot](screenshots/C05.png) |
| C06 | Narayan Swaroop Hospital, Prayagraj | Historical count on the operator's timeline. [Screenshot](screenshots/C06.png) |
| C07 | Peoples Trauma Hospital, Guntur | Operator's branch address on Old Club Road. [Screenshot](screenshots/C07.png) |
| C08 | Amritdhara My Hospital, Karnal | ITI Chowk branch and the 154+ lower bound. [Screenshot](screenshots/C08.png) |
| C09 | National Hospital, Kozhikode | Operator states 400 beds for its Kozhikode hospital. [Screenshot](screenshots/C09.png) |
| A01 | Holy Family Hospital, Bandra | Earlier public capture gives 268 beds. [Screenshot](../../../data/recovery/capacity-evidence/holy-family-bandra-beds.jpg) |

The held-out cases stay out of new capacity regression fitting. Previously frozen supplement datasets remain intact. The [Hindi call pilot](../../../non-ml/MASS_VERIFICATION.md#proposed-next-step-after-the-browser-review) describes a next step for unresolved cases, including branch confirmation, service questions and an administrator follow-up for scoped bed and doctor counts.

## Reproduce the review session

For a small pilot where capture is within the review budget, install the [experiment dependencies](../requirements.txt) and run from the repository root. `capture_public_evidence.py` opens the cited pages in an isolated Chrome profile and captures the full page. The queue script verifies screenshot hashes and packages the images with the original links. Extract the ZIP, open In The Loop, import the CSV and attach the images in `review-assets`. `record_in_the_loop.py` replays the historical assistant decisions for the pilot. A human review begins with the unlabeled CSV and exports its own decisions.

```sh
experiment=max-supplemented-data/experiments
review=max-supplemented-data/local/in-the-loop-review-replay
chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
python "$experiment/verification/capture_public_evidence.py" \
  --chrome "$chrome" --manifest "$experiment/verification/source_capture_manifest.json" \
  --output-dir "$review/captures"
python "$experiment/verification/prepare_queue.py" \
  --dataset max-supplemented-data/local/iteration-02/dataset \
  --evidence "$experiment/source_selection/evidence.json" \
  --captures "$review/captures" --output-dir "$review"
python "$experiment/verification/record_in_the_loop.py" \
  --chrome "$chrome" --app 'In The Loop Verification Tool' \
  --queue "$review/capacity-review-queue.csv" --evidence "$experiment/source_selection/evidence.json" \
  --captures "$review/captures" --output-dir "$review/desk-review"
```
