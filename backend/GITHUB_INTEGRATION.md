# GitHub integration (Phase 4)

This integration is server-side. The backend authenticates to GitHub with a fine-grained
personal access token or a GitHub App installation/user token. The token is never sent
to the browser or included in API responses.

## Configuration

Add these variables to the existing project-root `.env` file (do not commit it):

```dotenv
GITHUB_TOKEN=github_pat_your_token_here
GITHUB_API_URL=https://api.github.com
GITHUB_API_VERSION=2026-03-10
GITHUB_TIMEOUT_SECONDS=30
GITHUB_MAX_RETRIES=2
GITHUB_MAX_FILES_PER_PR=300
GITHUB_MAX_DIFF_BYTES=5000000
```

Create a fine-grained token restricted to only the repositories the service must review.
Grant:
- **Metadata: read-only** (repository discovery).
- **Pull requests: read-only** (read PR metadata, changed files, and diffs).
- **Pull requests: read and write** (required only to publish reviews and inline comments).

For private repositories, the token must be granted access to the selected repositories.
Never paste a token into source code, a chat, a frontend environment variable, or a committed file.

## API endpoints

All endpoints are under `/api/v1`.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/github/auth/status` | Reports whether a server-side token is configured (does not reveal it). |
| GET | `/github/user` | Returns the authenticated GitHub identity. |
| GET | `/github/repos` | Lists repositories visible to the configured identity. |
| GET | `/github/repos/{owner}/{repo}/pulls` | Lists pull requests (`state=open`, `closed`, or `all`). |
| POST | `/github/repos/{owner}/{repo}/pulls/{number}/review` | Fetches PR metadata, changed files and diff, then runs the existing AI review pipeline. |
| POST | `/github/reviews/{review_id}/submit` | Explicitly publishes a completed review and eligible inline comments. |

Example request to run an AI review:

```http
POST /api/v1/github/repos/OWNER/REPOSITORY/pulls/12/review
```

Example request to publish the completed review as a normal comment:

```json
{"event": "COMMENT"}
```

The submit event may be `COMMENT`, `REQUEST_CHANGES`, or `APPROVE`. The default is `COMMENT`.
Approval or requesting changes is never automatic: the caller must explicitly submit that event.

## Safety and behavior

- Pull-request head SHA is stored with the review. Submission is rejected if the PR head has
  changed since the AI review ran, preventing stale inline comments.
- Inline comments are emitted only when a finding's file and line can be verified against
  added lines in the current unified diff. Unverifiable locations remain in the review body.
- Finding paths are checked as safe relative repository paths before becoming inline comments.
- At most 50 inline comments are submitted in one review; the full findings summary remains
  in the review body.
- The diff size and changed-file count are bounded by configuration.
- GitHub 401/403/404/422 and rate-limit responses are mapped to actionable API errors.
  Rate-limited requests are not retried blindly. The API returns `retry_after_seconds` when
  GitHub supplies a retry/reset value.
- Network/timeout and transient 5xx failures use bounded retries.
- The integration does not clone repositories or execute repository code.

## Required runtime package

The GitHub REST client uses `httpx`. If it is not already installed in the project venv:

```powershell
python -m pip install httpx
```
