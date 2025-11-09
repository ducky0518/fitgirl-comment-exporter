A standalone Python utility that fetches and formats all **Comments, replies, and images** from any [FitGirl Repacks](https://fitgirl-repacks.site/) game page into a **beautiful self-contained HTML file**.

---

## ✨ Features

✅ **Full comment retrieval** — handles pagination and nested threads  
✅ **All replies included** — auto-follows `rootid` threads to fetch missing replies  
✅ **Embedded avatars** — all user avatars are downloaded and stored inline (no external requests)  
✅ **Inline image rendering** — detects `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` links and embeds them directly  
✅ **Theme toggle** — built-in Dark / Light / System switcher (with localStorage + OS auto-sync)  
✅ **Newest → oldest order** — top-level comments sorted newest first  
✅ **Fully offline HTML** — the generated file contains everything (CSS, JS, images, avatars, comments)  
✅ **Local timezone timestamps** — all comment times are automatically converted to your system timezone  
✅ **Zero dependencies beyond `requests`** — simple and portable

---

## 🖥️ Example Output

The generated HTML looks like this:

- Clean readable layout  
- User avatars, badges (Admin / Verified)  
- Nested reply threads  
- Inline screenshots and gifs  
- Theme buttons in top-right corner  
- “Generated” time and total comment count in the toolbar
- Dark theme example (auto-switches with OS preference)

---

## ⚙️ Installation

```bash
git clone https://github.com/ducky0518/fitgirl-comment-exporter.git
cd fitgirl-comment-exporter
pip install requests

## 🚀 Usage

Basic example:

```bash
python fitgirl_comment_exporter.py "https://fitgirl-repacks.site/session-skate-sim/"
```

Output:

```
✅ Wrote session-skate-sim-comments.html
   Roots: 50 • Total comments: 140 • Reported: 140 • Pages fetched: 3
```

### Optional Flags

| Flag | Description |
|------|--------------|
| `-o FILE.html` | Custom output filename |
| `--max-pages N` | Maximum number of paginated pages (default 400) |
| `--timeout SECONDS` | Per-request timeout (default 25) |
| `--user-agent "MyBot/1.0"` | Override default UA string |

---

## 📁 Output Example

Your resulting file (e.g. `session-skate-sim-comments.html`) is completely standalone:

- Double-click to open it in any browser.  
- Works offline.  
- Contains all comment text, avatars, and images.  
- Theme preference is remembered locally.

---

## 🧠 How It Works

1. Uses Tolstoy’s public JSON API:
   - `first` → loads the initial 50 comments  
   - `page` → fetches older comments (pagination)  
   - `rootid` → fetches missing replies per thread  
2. Downloads avatars and linked images as base64 `data:` URIs.  
3. Builds a full comment tree using `answer_comment_root_id` and inline reply tags.  
4. Generates a fully self-contained HTML file with inline CSS + JS.

---

## 🕐 Timezones

All timestamps are converted to your **local computer’s timezone** using Python’s `datetime.astimezone()`.  
The “Generated” toolbar time also reflects your local system clock.

---

## 🧩 Dependencies

- Python 3.8+
- [`requests`](https://pypi.org/project/requests/)


---

## 🤝 Contributing

Pull requests are welcome!  
Ideas for future improvements include:

- Optional JSON/Markdown output  
- Compact HTML mode  
- Filtering by user or date  
- Thread statistics summary  

---

## 🏁 Quick Start (TL;DR)

```bash
pip install requests
python fitgirl_export_comments.py "https://fitgirl-repacks.site/<any-game>/"
open <slug>-comments.html
```

Enjoy your fully browsable offline archive of FitGirl comment threads!
