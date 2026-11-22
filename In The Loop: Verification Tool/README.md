# In The Loop: Verification Tool

A static review workspace for comparing agent-collected screenshots with the data an agent scraped. Import a spreadsheet, inspect each screenshot beside its claimed values, and mark the record **incorrect**, **inconclusive**, or **correct**.

## Run locally

From this folder, with Python 3 installed:

```sh
python3 -m http.server 4173 --bind 127.0.0.1
```

Open <http://127.0.0.1:4173/>. Alternatively, use `npm run dev`. No package installation or build is required. Use an HTTP server rather than opening `index.html` as a `file://` URL, because browser modules and sample loading require an HTTP origin.

Run the automated tests with Node 22 or later:

```sh
npm test
```

## Host on GitHub Pages

The public, tool-only repository is [yana-157/in-the-loop-verification-tool](https://github.com/yana-157/in-the-loop-verification-tool). Its Pages address is [yana-157.github.io/in-the-loop-verification-tool](https://yana-157.github.io/in-the-loop-verification-tool/).

The workflow at [`.github/workflows/pages.yml`](.github/workflows/pages.yml) tests the app and stages its runtime files as the Pages site root. Tests, project documentation, package metadata, and other repository data are excluded from the deployment. The tool is also retained in the original Datathon repository under `In The Loop: Verification Tool/`; to host that copy independently, copy this folder's contents (including `.github` and `.nojekyll`) to a new repository root.

1. Have a repository administrator or maintainer open **Settings → Pages**.
2. Set **Build and deployment → Source** to **GitHub Actions**.
3. Push the application files and workflow to `main`, or run **Deploy In The Loop to GitHub Pages** from the Actions tab.
4. After a successful deployment, open <https://yana-157.github.io/in-the-loop-verification-tool/>.

The personal repository is public for GitHub Pages hosting. The original `augustchu/datathon-F26` repository remains private. Hosting Pages from a private repository requires an eligible GitHub plan, and enabling the publishing source requires administrator or maintainer access. See [GitHub's publishing-source instructions](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).

The workflow uploads only the application HTML, styles, scripts, favicon, library, vendored spreadsheet reader and license, sample spreadsheets, and sample screenshots. The colon and spaces in the source folder name do not become part of the deployed URL. The application uses relative resource URLs, so it also works under a GitHub Pages project subpath. `.nojekyll` prevents Jekyll processing when serving the directory directly.

This is a browser-only application. It needs no backend, GitHub token, database, or API keys. A private source repository does not by itself make the published Pages site private. Check the repository's Pages access settings before publishing sensitive evidence; the included sample captures will be served as site assets.

## Prepare the spreadsheet

Import `.csv`, `.tsv`, or `.xlsx`. For Excel, choose the worksheet to review. The import dialog lets you map a screenshot column and optional title and ID columns when headers differ from the defaults.

Each nonempty row represents one asset and its scraped claims:

| Column | Purpose |
| --- | --- |
| `id` | Optional stable, unique record identifier. IDs must not repeat. |
| `title` | Optional human-readable asset name. |
| `screenshot_path` | Required screenshot path or URL; alternate headers can be mapped during import. |
| `source_url` | Optional original webpage address. |
| Other columns | The actual scraped values to compare against the screenshot. Keep one claim per column for easy reading. |

For example:

```csv
id,title,screenshot_path,source_url,facility_name,bed_count
facility-001,Example facility,data/screenshots/facility-001.png,https://example.org/facility,Example facility,120
```

Screenshot references can be:

- A repository-relative path such as `data/screenshots/facility-001.png`, resolved using the repository and branch in import settings. The default is `augustchu/datathon-F26` on `main`.
- A public GitHub file URL such as `https://github.com/OWNER/PUBLIC-REPOSITORY/blob/main/screenshots/facility-001.png`.
- A raw image URL such as `https://raw.githubusercontent.com/OWNER/PUBLIC-REPOSITORY/main/screenshots/facility-001.png`.
- A public HTTP(S) image URL.
- An app-relative path beginning `./`, as used by the bundled sample spreadsheet.

GitHub screenshot links must be publicly fetchable. Because `augustchu/datathon-F26` is private, its raw URLs and repository-relative paths cannot be fetched anonymously by the browser. Signing in to GitHub in another tab does not provide this app with access to private raw files. The app does not request or store a GitHub token; never embed one in a screenshot URL or client-side file.

For private or unavailable images, use **Attach screenshots** to select matching local image files. Local file selections stay in the current browser session and must be reattached after a reload. If different screenshot paths share the same filename, attachment is rejected to avoid showing the wrong evidence; give those files unique names and update their spreadsheet paths. Alternatively, intentionally bundle screenshots in the app's deployed `samples/assets/` directory and reference them as `./samples/assets/filename.png`; those files then have the same visibility as the published site. The supplied samples use this approach and need no access to private GitHub raw URLs.

Bundled examples come from the repository's existing evidence captures. They illustrate the review workflow and begin unrated; displaying an example does not certify its scraped claims. See the sample provenance file for the source material.

## Review assets

The left sidebar has separate asset lists for pending, accepted, rejected, and inconclusive records. Only one list can expand at a time. Pick **1**, **10**, or **50** rows per list page, jump directly to an asset, or use the page arrows. Each list shows the current page and total page count.

The top tracker shows reviewed and remaining assets. All three decisions count as reviewed; inconclusive records remain available in their own list for follow-up. The central workspace displays the screenshot alongside the spreadsheet's scraped claims. The evenly spaced circular controls at the bottom mark the current record:

- **1 / red ×:** Incorrect; moves the record to rejected.
- **2 / purple ?:** Inconclusive.
- **3 / green ✓:** Correct; moves the record to accepted.

Use the previous/next asset controls or the left/right arrow keys to move through records. You can select any asset directly instead of following the original order. Rating does not automatically advance by default. Keyboard shortcuts are ignored while typing in form controls.

## Save and share decisions

Review state is saved in this browser's local storage, keyed to the dataset's content. Reloading restores the active workspace; importing the same unchanged source spreadsheet restores its local draft. A spreadsheet containing explicit `review_status` values takes precedence over an older local draft. Clearing browser data, changing browsers, or editing the source dataset can remove or separate that local state. There is no shared review account and no automatic write to GitHub or the source spreadsheet.

Use **Export reviews** to download a CSV containing original values and `review_status`, `review_notes`, and `reviewed_at`. Status values are `pending`, `accepted`, `rejected`, and `inconclusive`. Reimport that CSV to continue reviewing or hand it to another reviewer. Exports protect spreadsheet cells that could otherwise be interpreted as formulas, using a `review_csv_encoding` marker so reimport restores the original text. Additional canonical asset columns preserve custom ID, title, or screenshot mappings when necessary.

Excel parsing uses a pinned local copy of SheetJS Community Edition; see [vendor/README.md](vendor/README.md) and its included license.
