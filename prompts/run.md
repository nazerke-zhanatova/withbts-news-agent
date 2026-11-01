Run the withBTS.kz news editor now.

Follow CLAUDE.md exactly. Steps:
1. Read config/glossary.md, config/settings.yaml, state/candidates.json and state/posted.json.
2. If state/candidates.json is an empty array, stop: there is nothing to do.
3. Decide for each candidate: post or skip, using the hard rules.
4. Write queue/<id>.json for each post, and log every skip in state/skipped_log.json.
5. Finish with a one-paragraph summary in English of what you posted and skipped.

Do not use web search to add facts to a post. You may use web search or web fetch only to check whether
a news_media item is confirmed by an official source, and you must still credit only the sources in the candidates.
