Run the withBTS.kz news editor now.

Follow CLAUDE.md exactly. Steps:
1. Read config/glossary.md, config/settings.yaml, state/candidates.json and state/posted.json.
2. If state/candidates.json is an empty array, stop: there is nothing to do.
3. Decide for each candidate: post or skip, using the hard rules.
4. Write queue/<id>.json for each post, and log every skip in state/skipped_log.json.
5. Finish with a one-paragraph summary in English of what you posted and skipped.

Web search and web fetch: use them to check that news_media items are reported by at least 2 reliable outlets
or by an official source, to find a fitting photo (CLAUDE.md rule 5), and to read the full article behind a headline.
Every fact must come from a source you actually read, and every source you used goes into `credits`.
Never post the same news twice: always compare with state/posted.json first.
