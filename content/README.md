# content/

Drop `.txt` or `.docx` files here to inject your own content into the pipeline instead of using AI-generated topics.

## How it works

1. Add a `.txt` or `.docx` file to this folder
2. Run the pipeline (`python main.py`) — or push to GitHub to trigger the daily workflow
3. The pipeline reads your file, formats it into a video script, and produces Arabic + English videos
4. After processing, your file is moved to `content/processed/`

## File format

No special format required. Write naturally — the first line is used as the video topic/title.

**Example** (`my_topic.txt`):
```
How AI is changing the way we create music

AI tools like Suno and Udio can now generate full songs in seconds.
Artists are divided — some see it as a creative tool, others as a threat.
Here is what you need to know about the AI music revolution.
```

## Folders

| Folder | Purpose |
|---|---|
| `content/` | Drop your files here |
| `content/pending/` | Telegram bot drops — files sent via Telegram message or attachment |
| `content/processed/` | Files moved here after the pipeline uses them |

## Notes

- Supports `.txt` and `.docx` files
- For `.docx`, install: `pip install python-docx`
- One file = two videos (Arabic for TikTok, English for YouTube/Facebook)
- If no files are present, the pipeline falls back to AI research as normal
