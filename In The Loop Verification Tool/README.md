# In The Loop Verification Tool

A static review workspace for comparing agent-collected screenshots with scraped spreadsheet data. Inspect the evidence beside its claimed values, then mark each asset **incorrect**, **inconclusive**, or **correct**.

- [Open the GitHub Pages app](https://yana-157.github.io/in-the-loop-verification-tool/)
- [Public tool repository](https://github.com/yana-157/in-the-loop-verification-tool)
- [Project report and compute comparison](REPORT.md)

Open the same app with `?mode=links` or choose **Link-only review** to open source pages directly. Import a spreadsheet with a source URL and the claims for each row. **Open original page** opens a new tab. Record and export decisions in the same way as in screenshot mode. The two versions keep separate drafts in the browser. [Link-only CSV template](samples/link-template.csv)

The included sample consists of five real screenshots and their scraped claims from public Faridabad Medical Center and Arti Hospital webpages. They were explicitly approved for inclusion in this public tool repository. These examples begin unrated; displaying a sample does not certify its claims. See [sample provenance](samples/README.md). Your own imported spreadsheets and review decisions stay in your browser.

## Run locally

With Python 3 installed, run from this directory:

```sh
python3 -m http.server 4173 --bind 127.0.0.1
```

Open <http://127.0.0.1:4173/>. Alternatively, use `npm run dev`. There are no dependencies to install and no build step. Use an HTTP server rather than opening `index.html` with `file://`, because JavaScript modules and sample loading require an HTTP origin.

Open the local app with `?mode=links` for a spreadsheet without screenshots. Use the exact source URL, including an article section or PDF page fragment when available. Enter a source column manually during import if the sheet uses a custom header. For a URL without a fragment, add `source_pdf_page` for an exact PDF page or `source_anchor_text` for an exact phrase the browser can locate. [Text fragments](https://wicg.github.io/ScrollToTextFragment/) · [Adobe PDF page links](https://helpx.adobe.com/ca/acrobat/kb/link-html-pdf-page-acrobat.html)

Run tests with Node 22 or later:

```sh
npm test
```

## Deploy to GitHub Pages

The [Pages workflow](.github/workflows/pages.yml) runs the tests, stages only runtime assets, and publishes the app when `main` changes. It can also be run manually from the Actions tab.

For a new repository:

1. Copy this folder's contents to the repository root, including `.github` and `.nojekyll`.
2. In **Settings → Pages → Build and deployment**, select **GitHub Actions**.
3. Push to `main` and wait for **Deploy In The Loop to GitHub Pages** to finish.
4. Open `https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/`.

The app uses relative resource URLs and works under a project subpath. Tests, documentation, and package metadata are excluded from the deployment. No backend, database, GitHub token, or API keys are needed. See [GitHub's publishing-source documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).

## Prepare a spreadsheet

Import `.csv`, `.tsv`, or `.xlsx` files up to 20 MB and 25,000 rows. Choose a worksheet for Excel files. The import dialog lets you map a screenshot column and optional title and ID columns when headers differ from the defaults.

Each nonempty row represents one screenshot and its scraped claims:

| Column | Purpose |
| --- | --- |
| `id` | Optional stable, unique record identifier. |
| `title` | Optional human-readable asset name. |
| `screenshot_path` | Required screenshot path or URL; alternate headers can be mapped. |
| `source_url` | Original webpage address for the screenshot. Use it for each full-page review asset. Older imports may leave it blank. |
| `candidate_source_url` | Optional independent source for the particular candidate claim. |
| Other columns | Scraped values to compare against the screenshot. |

The app also recognizes `full_page_screenshot_path` and `evidence_source_url` in completed evidence worklists. Supply a screenshot path and its page URL before importing a row for review.

```csv
id,title,screenshot_path,source_url,facility_name,bed_count
example-001,Example facility,screenshots/example-001.png,https://example.com/facility,Example facility,120
```

Screenshot references can be:

- A repository-relative path such as `screenshots/example-001.png`, resolved against the repository and branch entered during import.
- A public GitHub file link such as `https://github.com/OWNER/REPO/blob/main/screenshots/example-001.png`.
- A raw GitHub URL or another public HTTP(S) image URL.
- An app-relative path beginning `./`, as used by the bundled sample.

Private GitHub files cannot be loaded anonymously. Use **Attach screenshots** to select matching local image files instead. These files stay in the current browser session and must be reattached after a reload. If different paths share the same filename, attachment is rejected to avoid showing the wrong evidence; give those files unique names and update the spreadsheet. The app never asks for a GitHub token.

## Compare multiple source claims

In link-only mode, put each publisher's claim on its own row. Use the same `case_id`, `facility_id`, or `branch_id` and the same `field` for sources describing the same branch and item. Add `value`, `publisher`, `source_url` and `source_date` when known. The comparison panel lists the published values, dates and links. The sidebar highlights rows whose sources differ. Choose **Differences only** to filter the queues. The exported `review_source_comparison` records `different`, `agree`, or `single-source` alongside each review decision. [Example across two publishers](samples/link-template.csv)

## Review assets

The sidebar has separate lists for pending, accepted, rejected, and inconclusive assets. Only one can expand at a time. Each list supports **1**, **10**, or **50** assets per page, previous/next page arrows, a page selector, search, and direct selection of any asset.

The top tracker shows reviewed and remaining counts. All three decisions count as reviewed. Use a full-page screenshot and scroll inside the image panel. **Fit width** resets its zoom, and **Expand screenshot** opens a larger scrollable view with an **Actual size** toggle. **Open original page** opens the `source_url` in a new tab beside either view. A supplied `candidate_source_url` links from the corresponding claim field.

- **1 / red ×:** Incorrect; moves the asset to rejected.
- **2 / purple ?:** Inconclusive.
- **3 / green ✓:** Correct; moves the asset to accepted.

Use the previous/next asset buttons or left/right arrow keys to navigate. Add an optional note, revise a rating, or clear it to return the asset to the pending queue. Rating does not automatically advance unless **Advance after rating** is enabled. Shortcuts are ignored while typing or while a dialog is open.

## Test scraper accuracy with a random sample

Import the complete sheet whose extraction you want to evaluate. Use one row per audit item and include the original publisher's page or a dated capture from the scrape. Set a consistent acceptance rule for the claim columns you are checking. **Scraper accuracy test** works with both source links and screenshots.

1. Set the lowest accuracy you want the test to exceed, the expected accuracy used to plan the sample, the significance level α and desired power. The default test uses a 90% threshold, 98% planning accuracy, 5% α and 80% power. α is the maximum chance of passing a sheet at or below the threshold. Power is the chance of passing at the planning accuracy under the stated sample design.
2. Choose **Calculate sample** to see the minimum number of randomly selected rows and the confirmed-correct cutoff. The app uses a one-sided exact hypergeometric test for the imported finite sheet, drawing rows uniformly without replacement. It calculates the smallest fixed sample that meets the selected α and power. Small sheets can require reviewing every row. Choose the settings before viewing the sampled results. [Hypergeometric distribution reference](https://www.stat.ethz.ch/R-manual/R-devel/library/stats/help/rhyper.html)
3. Choose **Freeze random sample**. The sample starts with fresh ratings and keeps the same rows and settings when reopened. Review each selected row against the cited source as it was when scraped. If the publisher clearly reported the scraped claim, mark it correct even when another publisher disagrees. Mark copied values that contradict the cited source incorrect. Mark ambiguous text, uncertain dates or inaccessible evidence inconclusive, and keep those rows in the sample.
4. After every sampled row has a decision, the result states whether the sheet's accuracy exceeds the threshold under the test. If any rows are inconclusive, the app computes the p-value range from treating them all as correct to treating them all as incorrect. A pass requires the conclusion to hold at both ends of that range. If both ends fall short, the app reports that accuracy above the threshold was not demonstrated. If the result changes across the range, the whole audit is inconclusive.

The test checks **output-first row fidelity** within the imported sheet. With 161 rows and the defaults above, the minimum is **39 rows**, with **38 confirmed correct** needed to pass. For recall, draw an independent source-first sample of eligible published records and check which appear in the scraper output. **Export audit receipt** saves the plan, ordered sample, review decisions and test result in JSON. Regular reviews and the source comparison remain separate from the audit.

## Save and export

Review state is saved in this browser's local storage, keyed to the dataset's content. Reloading restores the active workspace. Reimporting an unchanged source spreadsheet restores its local draft; explicit `review_status` values in an imported review export take precedence. Clearing browser data or changing browsers removes access to that local state. There is no automatic write to GitHub or the source spreadsheet.

**Export reviews** downloads the original data plus `review_status`, `review_notes`, and `reviewed_at`. Reimport this CSV to continue elsewhere. The statuses are `pending`, `accepted`, `rejected`, and `inconclusive`. A `review_csv_encoding` marker enables reversible protection against spreadsheet formulas; canonical asset columns preserve custom mappings when necessary.

Excel parsing uses a pinned local copy of SheetJS Community Edition. See [vendor/README.md](vendor/README.md) and its included license.
