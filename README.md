# Bitbucket Snippets Git Backup Tool

This Python script backs up Bitbucket snippets to a local Git repository.

By default, it backs up only the latest revision of each
snippet.

Optionally, it can back up the full revision history. The script aims to preserve original authorship and commit dates from
Bitbucket in the Git history.

## Features

- Backs up snippets from a specified Bitbucket workspace.
- Supports authentication via Bitbucket username and App Password.
- Creates a local Git repository to store the snippets.
- Organizes snippets into directories named after a sanitized version of their title and unique ID (e.g.,
  `My_Awesome_Snippet_asdf1234/`).
- Backs up all files within each snippet.
- **Default Behavior**: Backs up only the latest revision of each snippet.
- **Optional Historical Backup**: Can back up all historical revisions as distinct Git commits using the `--historical` flag.
- Preserves (where possible):
    - Original author information (name parsed from Bitbucket data).
    - Original commit date for both author and committer timestamps in Git.
- Option to backup specific snippet IDs.
- Generates a root `README.md` file with links to individual snippet READMEs.
- Generates a `README.md` within each snippet directory, listing its files (from the latest backed-up revision) and a link to the
  original snippet on Bitbucket.
- Configurable Git committer name/email for backup commits.
- Basic retry logic for API requests with exponential backoff for common server-side issues and rate limiting.

## Prerequisites

- Python 3.8+
- Git installed and accessible in your system's PATH.
- A Bitbucket App Password with at least `snippet` (read) permissions.

## Usage

1. [Create a Bitbucket App password](https://support.atlassian.com/bitbucket-cloud/docs/create-an-app-password/).
1. Install the script dependencies either globally or in a virtual environment:

   **Option 1 (global)**

         pip install .

   **Option 2 (virtual environment)**

         python -m venv venv
         source venv/bin/activate
         python -m pip install .

1. Run the script from the command line.

         python backup_bitbucket_snippets.py --workspace <your_workspace_slug> \
                                             --auth-user <your_bitbucket_username> \
                                             --auth-pass <your_app_password> \
                                             [options]

### Command-Line Arguments

- `--workspace` (Required): Your Bitbucket workspace ID (slug). Snippets from this workspace will be targeted if `--snippet-ids` is
  not used. Also used as context for fetching specific snippets if their workspace info is missing.
- `--auth-user` (Required): Your Bitbucket username for authentication.
- `--auth-pass` (Required): Your Bitbucket App Password.
- `--output-dir` (Optional): The local directory where the Git backup repository will be created/updated. Defaults to
  `bitbucket_snippets_backup`.
- `--api-base-url` (Optional): The base URL for the Bitbucket API. Defaults to `https://api.bitbucket.org/2.0`.
- `--role` (Optional): Filter initial snippet list by role (`owner`, `contributor`, or `member`) when `--snippet-ids` is not used.
- `--historical` (Optional): If specified, backup all historical revisions of each snippet. By default (if this flag is not
  present), only the latest revision of each snippet is backed up.
- `--snippet-ids` (Optional): A comma-separated list of specific snippet IDs (the encoded_id from Bitbucket, e.g., `id1,id2`) to
  back up. If provided, only these snippets will be processed, and the general listing for the workspace (and `--role`) will be
  ignored. Example: `--snippet-ids "abcdef12,ghijkl34"`
- `--committer-name` (Optional): Name to use for the Git committer for backup commits. Defaults to "Snippet Backup Script".
- `--committer-email` (Optional): Email to use for the Git committer for backup commits. Defaults to "
  backup@bitbucket-script.local".

### Examples

#### Backup only the latest revision of all snippets in a workspace:

```
python backup_bitbucket_snippets.py --workspace myteamworkspace \
                                    --auth-user mybitbucketuser \
                                    --auth-pass YOUR_APP_PASSWORD
```

#### Backup all historical revisions for specific snippets:

```
python backup_bitbucket_snippets.py --workspace myteamworkspace \
                                    --auth-user mybitbucketuser \
                                    --auth-pass YOUR_APP_PASSWORD \
                                    --snippet-ids "kypj,anotherId" \
                                    --historical
```

#### Backup latest revision, specifying custom committer and output directory:

```
python backup_bitbucket_snippets.py --workspace mypersonalspace \
                                    --auth-user mybitbucketuser \
                                    --auth-pass YOUR_APP_PASSWORD \
                                    --output-dir ./archive/snippets \
                                    --committer-name "Automated Backup" \
                                    --committer-email "backup-bot@my.domain"
```

## Backup Directory Structure

The backup will be organized as follows:

```
<output-dir>/
├── .git/                               # The Git repository
├── README.md                           # Root README with links to all snippets
├── Sanitized_Snippet_Title_One_id123/  # Directory for the first snippet
│   ├── README.md                       # README specific to this snippet
│   ├── file1.py
│   └── image_with_&_symbol.png
└── Another_Snippet_Title_idabc/        # Directory for another snippet
    ├── README.md
    └── notes_file.txt
└── ...
```

## Git Commit History

- **Latest Only (Default):** One commit per snippet, representing its latest state.
- **Historical (`--historical` flag):** Each revision of a snippet from Bitbucket is translated into a separate Git commit.
- **Commit Dates:** Both the author date and committer date of the Git commits are set to the original timestamp of the snippet
  revision on Bitbucket.
- **Author:** The Git commit author is set to the original author of the snippet revision (name parsed from Bitbucket data, email is
  a placeholder if not directly available).
- **Committer:** The Git committer details are configurable via CLI args (defaults to "Snippet Backup Script" / "
  backup@bitbucket-script.local"). This helps distinguish automated backup commits from original authorship.
- **Commit Messages:** Include the snippet title, ID, original revision hash (if applicable), and the original commit message.

## Notes and Limitations

- **API Rate Limiting:** Bitbucket's API has rate limits. For a very large number of snippets or revisions, the script might hit
  these limits. The script includes basic retry logic but extensive backups might require more sophisticated handling or running in
  batches.
- **Filename Sanitization:** Snippet titles (for directory names) are sanitized. Original filenames within snippets are preserved.
  Links in generated README files are URL-encoded to handle special characters.
- **Error Handling:** The script includes error handling for API requests and Git commands. Check the console output for any issues.
- **Binary Files:** Binary files are supported.
- **Empty Snippets/No Commits:** Snippets that are empty or have no explicit commit history are backed up with a single commit
  representing their latest known state.
- **File Deletions (Historical Mode):** When backing up with `--historical`, the script attempts to handle file deletions between
  snippet revisions using `git rm`.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
