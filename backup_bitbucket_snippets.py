import argparse
import os
import re
import subprocess
import time
import urllib.parse
from datetime import datetime, timezone
from functools import wraps

import requests


# --- Retry Decorator ---
def retry_api_call(max_retries=3, initial_delay=2, backoff_factor=2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempts = 0
            current_delay = initial_delay
            while attempts < max_retries:
                try:
                    result = func(*args, **kwargs)
                    # Allow None for certain cases like no data found or fetch_file_content
                    if result is None and func.__name__ not in ['fetch_file_content', 'fetch_from_bitbucket_paginated',
                                                                'fetch_single_from_bitbucket']:
                        pass
                    return result  # Success
                except requests.exceptions.RequestException as e:
                    attempts += 1
                    status_code = e.response.status_code if hasattr(e, 'response') and e.response is not None else None
                    print(f"    API call {func.__name__} failed (attempt {attempts}/{max_retries}): {e}. Status: {status_code}")

                    if attempts == max_retries:
                        print(f"    Max retries reached for {func.__name__}. Error: {e}")
                        return None

                    should_retry = False
                    if status_code in [429, 500, 502, 503, 504]:
                        should_retry = True
                        retry_after_header = e.response.headers.get("Retry-After") if hasattr(e,
                                                                                              'response') and e.response is not None else None
                        if retry_after_header:
                            try:
                                wait_time = int(retry_after_header)
                                print(f"    Honoring Retry-After header: waiting {wait_time}s")
                                current_delay = wait_time  # Override calculated delay
                            except ValueError:
                                print(
                                    f"    Could not parse Retry-After header ('{retry_after_header}'). Using exponential backoff.")
                                current_delay = initial_delay * (backoff_factor ** (attempts - 1))
                        else:
                            current_delay = initial_delay * (backoff_factor ** (attempts - 1))
                    elif status_code is None:  # Network issue
                        should_retry = True
                        current_delay = initial_delay * (backoff_factor ** (attempts - 1))

                    if not should_retry:
                        print(f"    Non-retryable HTTP error for {func.__name__}. Status: {status_code if status_code else 'N/A'}")
                        # For 404s from main fetch functions, we might not want to retry and just return None
                        if status_code == 404 and func.__name__ in ['fetch_from_bitbucket_paginated',
                                                                    'fetch_single_from_bitbucket']:
                            return None  # No data found
                        return None

                    print(f"    Retrying in {current_delay}s...")
                    time.sleep(current_delay)
            return None

        return wrapper

    return decorator


# --- Helper Functions ---

@retry_api_call()
def fetch_from_bitbucket_paginated(endpoint, auth_user, auth_password, api_base_url="https://api.bitbucket.org/2.0"):
    """Fetches paginated data from Bitbucket API."""
    url = f"{api_base_url}{endpoint}"
    all_values = []
    page_count = 0
    max_pages = 100

    while url and page_count < max_pages:
        page_count += 1
        print(f"Fetching page {page_count}: {url}")
        response = requests.get(url, auth=(auth_user, auth_password))
        response.raise_for_status()
        data = response.json()

        if "values" in data:
            all_values.extend(data["values"])
            url = data.get("next")
            if not url: break
        else:
            print(f"Warning: Expected paginated response from {endpoint} but got single object. Processing as is.")
            if isinstance(data, list):
                all_values.extend(data)
            elif isinstance(data, dict):
                all_values.append(data)
            break

    return {"values": all_values}


@retry_api_call()
def fetch_single_from_bitbucket(endpoint, auth_user, auth_password, api_base_url="https://api.bitbucket.org/2.0"):
    """Fetches a single resource (not paginated) from Bitbucket API."""
    url = f"{api_base_url}{endpoint}"
    print(f"Fetching single: {url}")
    response = requests.get(url, auth=(auth_user, auth_password))
    response.raise_for_status()
    return response.json()


@retry_api_call()
def fetch_file_content(url, auth_user, auth_password):
    print(f"Fetching file: {url}")
    response = requests.get(url, auth=(auth_user, auth_password))
    response.raise_for_status()
    return response.content


# ... (rest of the script from the previous response remains the same) ...

def sanitize_directory_name(name):
    name = str(name)
    name = re.sub(r'[<>:"/\\|?*\n\r\t\x00-\x1f]', '', name)
    name = re.sub(r'[\s_.-]+', '_', name)
    return name[:150]


def run_git_command(command_list, cwd, env_vars=None):
    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)
    try:
        process = subprocess.Popen(command_list, cwd=cwd, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            if not (command_list[0:2] == ["git", "commit"] and (
                    "nothing to commit" in stderr.lower() or "no changes added to commit" in stderr.lower() or "clean, working tree clean" in stderr.lower())):
                print(f"  Error running Git command: {' '.join(command_list)}")
                if stdout: print(f"  Stdout: {stdout.strip()}")
                if stderr: print(f"  Stderr: {stderr.strip()}")
            return False, stderr
        return stdout, None
    except Exception as e:
        print(f"  Exception running Git command: {e}")
        return False, str(e)


def setup_local_repo(repo_path):
    if not os.path.exists(repo_path):
        os.makedirs(repo_path)
    if not os.path.exists(os.path.join(repo_path, ".git")):
        print(f"Initializing Git repository in {repo_path}...")
        run_git_command(["git", "init"], cwd=repo_path)
    else:
        print(f"Using existing Git repository in {repo_path}.")


def generate_snippet_readme(snippet_dir_path, snippet_title, snippet_id, bitbucket_html_link, files_in_latest_commit):
    readme_path = os.path.join(snippet_dir_path, "README.md")
    content = f"# {snippet_title}\n\n"
    content += f"**Original Snippet ID:** `{snippet_id}`\n"
    if bitbucket_html_link:
        content += f"**Bitbucket Link:** [{snippet_title}]({bitbucket_html_link})\n\n"
    content += "## Files in this Snippet\n\n"
    if files_in_latest_commit:
        for f_path in sorted(list(files_in_latest_commit)):
            filename_display = os.path.basename(f_path)
            encoded_f_path_parts = [urllib.parse.quote(part) for part in f_path.split(os.path.sep)]
            encoded_f_path = "/".join(encoded_f_path_parts)
            content += f"- [{filename_display}](./{encoded_f_path})\n"
    else:
        content += "No files found in the latest revision of this snippet.\n"

    try:
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(content)
    except IOError as e:
        print(f"    Error writing README.md for snippet '{snippet_title}': {e}")


def generate_root_readme(repo_path, backed_up_snippets_info):
    readme_path = os.path.join(repo_path, "README.md")
    content = "# Bitbucket Snippets Backup\n\n"
    content += f"This repository contains a backup of Bitbucket snippets, last updated on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}.\n\n"
    content += "## Snippets Index\n\n"

    if backed_up_snippets_info:
        for info in sorted(backed_up_snippets_info, key=lambda x: x['title'].lower()):
            dir_link = urllib.parse.quote(info['dir_name'])
            content += f"- [{info['title']} (ID: {info['id']})]({dir_link}/README.md)\n"
    else:
        content += "No snippets have been backed up yet or an error occurred.\n"

    try:
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"\nGenerated root README.md at {repo_path}")
    except IOError as e:
        print(f"\nError writing root README.md: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Backup BitBucket Snippets to a local Git repository.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--auth-user", required=True, help="Bitbucket username for authentication.")
    parser.add_argument("--auth-pass", required=True, help="Bitbucket App Password for authentication.")
    parser.add_argument("--workspace",
                        help="Bitbucket workspace slug (username or team ID). If not provided, defaults to the value of --auth-user.")
    parser.add_argument("--output-dir", default="bitbucket_snippets_backup",
                        help="Local directory to store the Git backup (default: bitbucket_snippets_backup).")
    parser.add_argument("--api-base-url", default="https://api.bitbucket.org/2.0",
                        help="Bitbucket API base URL (default: https://api.bitbucket.org/2.0).")
    parser.add_argument("--role",
                        help="Optional: Filter initial snippet list by role (owner, contributor, member) if not using --snippet-ids.")
    parser.add_argument("--historical", action='store_true', default=False,
                        help="Backup all historical revisions of each snippet. Default is to backup only the latest revision.")
    parser.add_argument("--snippet-ids",
                        help="Comma-separated list of specific snippet IDs (encoded_id) to backup (e.g., \"id1,id2\"). Overrides general listing if provided.")
    parser.add_argument("--committer-name", default="Snippet Backup Script",
                        help="Name to use for the Git committer (default: \"Snippet Backup Script\").")
    parser.add_argument("--committer-email", default="backup@bitbucket-script.local",
                        help="Email to use for the Git committer (default: \"backup@bitbucket-script.local\").")

    args = parser.parse_args()

    # Default workspace to auth-user if not supplied
    if args.workspace is None:
        args.workspace = args.auth_user
        print(f"INFO: No --workspace provided, defaulting to --auth-user: {args.workspace}")

    local_repo_path = args.output_dir
    setup_local_repo(local_repo_path)

    all_backed_up_snippets_info = []

    snippets_to_process = []
    if args.snippet_ids:
        ids_list = [s_id.strip() for s_id in args.snippet_ids.split(',')]
        print(f"Fetching specific snippet IDs: {ids_list} from workspace {args.workspace}")
        for s_id in ids_list:
            snippet_detail_endpoint = f"/snippets/{args.workspace}/{s_id}"
            snippet_detail = fetch_single_from_bitbucket(snippet_detail_endpoint, args.auth_user, args.auth_pass, args.api_base_url)
            if snippet_detail and 'type' in snippet_detail and snippet_detail['type'] == 'snippet':
                snippets_to_process.append(snippet_detail)
            else:
                print(f"Could not fetch details for snippet ID: {s_id} in workspace {args.workspace}. Response: {snippet_detail}")
    else:
        snippets_list_endpoint = f"/snippets/{args.workspace}"
        if args.role:
            snippets_list_endpoint += f"?role={args.role}"
        print(f"Fetching snippets from workspace: {args.workspace} ...")  # (Endpoint: {snippets_list_endpoint})
        snippets_data = fetch_from_bitbucket_paginated(snippets_list_endpoint, args.auth_user, args.auth_pass, args.api_base_url)
        if snippets_data and "values" in snippets_data:
            snippets_to_process = snippets_data["values"]

    if not snippets_to_process:
        print("No snippets found or specified to process.")
        generate_root_readme(local_repo_path, all_backed_up_snippets_info)
        return

    for snippet_info in snippets_to_process:
        snippet_id = snippet_info.get("id")
        if not snippet_id:
            print(f"Warning: Snippet data missing 'id'. Data: {snippet_info}")
            continue

        snippet_title = snippet_info.get("title", f"Untitled_Snippet_{snippet_id}")
        sanitized_title_part = sanitize_directory_name(snippet_title)
        snippet_folder_name = f"{sanitized_title_part}_{snippet_id}"

        bitbucket_html_link = snippet_info.get("links", {}).get("html", {}).get("href")

        workspace_slug_for_snippet_api = args.workspace
        if "workspace" in snippet_info and "slug" in snippet_info["workspace"]:
            workspace_slug_for_snippet_api = snippet_info["workspace"]["slug"]
        elif "owner" in snippet_info and "nickname" in snippet_info["owner"]:
            workspace_slug_for_snippet_api = snippet_info["owner"]["nickname"]

        print(f"\nProcessing snippet: '{snippet_title}' (ID: {snippet_id}, Folder: {snippet_folder_name})")

        snippet_files_base_dir = os.path.join(local_repo_path, snippet_folder_name)
        if not os.path.exists(snippet_files_base_dir):
            os.makedirs(snippet_files_base_dir)

        current_files_in_snippet_dir_for_readme = set()

        commits_endpoint = f"/snippets/{workspace_slug_for_snippet_api}/{snippet_id}/commits"
        print(f"  Fetching commits for snippet {snippet_id}...")
        commits_data = fetch_from_bitbucket_paginated(commits_endpoint, args.auth_user, args.auth_pass, args.api_base_url)

        commits_to_process = []
        if commits_data and "values" in commits_data and commits_data["values"]:
            try:
                # Ensure dates are actual datetime objects for sorting if not already
                for c_detail in commits_data["values"]:
                    if isinstance(c_detail.get("date"), str):
                        c_detail["date_obj"] = datetime.fromisoformat(c_detail["date"].replace("Z", "+00:00"))
                    else:  # Should not happen if API is consistent
                        c_detail["date_obj"] = datetime.now(timezone.utc)

                sorted_commits = sorted(commits_data["values"], key=lambda c: c["date_obj"])

                if args.historical:
                    commits_to_process = sorted_commits
                elif sorted_commits:
                    commits_to_process = [sorted_commits[-1]]
                else:  # No commits, but not historical handled by block below
                    print(f"    No commit history found for snippet {snippet_id}. Processing current state as 'latest'.")
            except (TypeError, KeyError, ValueError) as e:
                print(
                    f"    Error sorting/processing commits for snippet {snippet_id}. Error: {e}. Will attempt to process latest state only.")

        if not commits_to_process:
            print(
                f"    No historical commits to process or only latest requested. Processing current state of snippet {snippet_id}.")
            owner_info = snippet_info.get("owner", {})  # Use owner as more general than creator for snippets
            author_name = owner_info.get("nickname", owner_info.get("display_name", args.committer_name))
            author_email = "author@example.com"

            # Use snippet's updated_on or created_on for the single commit date
            latest_date = snippet_info.get("updated_on", snippet_info.get("created_on", datetime.now(timezone.utc).isoformat()))

            commits_to_process = [{
                "hash": snippet_info.get("id"),  # Use snippet_id as revision for HEAD
                "date": latest_date,
                "author": {"raw": f"{author_name} <{author_email}>", "nickname": author_name, "display_name": author_name},
                "message": f"Latest state of snippet '{snippet_title}' as of {latest_date}"
            }]
            # To fetch files for this "latest" state, we'll use the main snippet_info
            # or fetch the HEAD revision of the snippet explicitly.
            # The endpoint `/snippets/{workspace}/{encoded_id}/{node_id}` where node_id can be HEAD or the latest commit hash.
            # For simplicity, if snippet_info itself contains 'files', we use that.
            # If not, we'd fetch `/snippets/{workspace_slug_for_snippet_api}/{snippet_id}/HEAD`
            # However, the main snippet_info usually has the HEAD files.

        for i, commit_details in enumerate(commits_to_process):
            commit_sha = commit_details["hash"]
            commit_date_str_raw = commit_details["date"]
            try:
                commit_datetime_obj = datetime.fromisoformat(commit_date_str_raw.replace("Z", "+00:00"))
                commit_date_for_git = commit_datetime_obj.isoformat()
            except ValueError:
                print(f"      Warning: Could not parse date '{commit_date_str_raw}' for commit {commit_sha}. Using current time.")
                commit_date_for_git = datetime.now(timezone.utc).isoformat()

            commit_author_obj = commit_details.get("author", {})
            raw_author_str = commit_author_obj.get("raw")
            commit_author_name = args.committer_name
            commit_author_email = args.committer_email

            if raw_author_str and '<' in raw_author_str and '>' in raw_author_str:
                parts = raw_author_str.split('<', 1)
                commit_author_name = parts[0].strip() or commit_author_name
                commit_author_email = parts[1].replace('>', '').strip() or commit_author_email
            elif raw_author_str:
                commit_author_name = raw_author_str.strip() or commit_author_name
            elif "nickname" in commit_author_obj:
                commit_author_name = commit_author_obj.get("nickname", commit_author_name)
            elif "display_name" in commit_author_obj:
                commit_author_name = commit_author_obj.get("display_name", commit_author_name)

            commit_message_summary = commit_details.get("message", f"Backup of revision {commit_sha[:7]}")
            commit_message_summary = commit_message_summary.replace('"', "'").replace('\n', ' ')

            print(f"    Processing revision: {commit_sha[:12]} dated {commit_date_for_git}")

            # Determine the source of file data for this commit
            # If this is a synthesized "latest" commit, revision_data might be snippet_info itself
            if commit_sha == snippet_info.get("id") and 'files' in snippet_info:  # Heuristic for synthesized latest commit
                revision_data = snippet_info
                print("      Using files from main snippet object for latest state.")
            else:
                revision_details_endpoint = f"/snippets/{workspace_slug_for_snippet_api}/{snippet_id}/{commit_sha}"
                revision_data = fetch_single_from_bitbucket(revision_details_endpoint, args.auth_user, args.auth_pass,
                                                            args.api_base_url)

            if not revision_data or "files" not in revision_data or not isinstance(revision_data.get("files"), dict):
                print(f"      Could not get valid file list for revision {commit_sha}. Skipping this revision's files.")
                # If it's the last/only revision, we still want to add info for README generation
                if i == len(commits_to_process) - 1:
                    current_files_in_snippet_dir_for_readme = set()  # No files could be determined
                continue  # Skip file processing for this commit

            files_in_this_commit_relative_paths = set()

            for filename_in_snippet in revision_data["files"].keys():
                relative_file_path = filename_in_snippet
                files_in_this_commit_relative_paths.add(relative_file_path)
                local_file_path = os.path.join(snippet_files_base_dir, relative_file_path)

                file_content_endpoint = f"/snippets/{workspace_slug_for_snippet_api}/{snippet_id}/{commit_sha}/files/{urllib.parse.quote(filename_in_snippet)}"
                actual_file_url = f"{args.api_base_url}{file_content_endpoint}"

                file_content = fetch_file_content(actual_file_url, args.auth_user, args.auth_pass)

                if file_content is not None:
                    os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
                    try:
                        file_content_str = file_content.decode('utf-8')
                        with open(local_file_path, "w", encoding="utf-8", newline='') as f:
                            f.write(file_content_str)
                    except UnicodeDecodeError:
                        with open(local_file_path, "wb") as f:
                            f.write(file_content)

            if args.historical:
                if os.path.exists(snippet_files_base_dir):
                    for root_dir, _, files_in_git_dir in os.walk(snippet_files_base_dir):
                        for f_name_git in files_in_git_dir:
                            if f_name_git == "README.md": continue
                            full_path_in_git_fs = os.path.join(root_dir, f_name_git)
                            relative_path_in_git_fs = os.path.relpath(full_path_in_git_fs, snippet_files_base_dir).replace(
                                os.path.sep, "/")
                            if relative_path_in_git_fs not in files_in_this_commit_relative_paths:
                                git_path_to_remove = os.path.join(snippet_folder_name, relative_path_in_git_fs).replace(os.path.sep,
                                                                                                                        "/")
                                run_git_command(["git", "rm", git_path_to_remove], cwd=local_repo_path)

            if i == len(commits_to_process) - 1:
                current_files_in_snippet_dir_for_readme = files_in_this_commit_relative_paths

            run_git_command(["git", "add", snippet_folder_name], cwd=local_repo_path)

            commit_env = {
                "GIT_AUTHOR_NAME": commit_author_name,
                "GIT_AUTHOR_EMAIL": commit_author_email,
                "GIT_AUTHOR_DATE": commit_date_for_git,
                "GIT_COMMITTER_NAME": args.committer_name,
                "GIT_COMMITTER_EMAIL": args.committer_email,
                "GIT_COMMITTER_DATE": commit_date_for_git
            }

            final_commit_message = f"Snippet: {snippet_title} (ID: {snippet_id})\nRev: {commit_sha[:7]}\n\n{commit_message_summary}"

            status_output, _ = run_git_command(["git", "status", "--porcelain", snippet_folder_name], cwd=local_repo_path)
            if status_output and status_output.strip():
                _, commit_stderr = run_git_command(["git", "commit", "--allow-empty", "-m", final_commit_message],
                                                   cwd=local_repo_path, env_vars=commit_env)
                if commit_stderr and (
                        "nothing to commit" in commit_stderr.lower() or "no changes added to commit" in commit_stderr.lower()):
                    pass  # It's fine, sometimes git says this even if something was staged with allow-empty
            else:
                print(f"      No file changes to commit for revision {commit_sha[:7]}.")

        generate_snippet_readme(snippet_files_base_dir, snippet_title, snippet_id, bitbucket_html_link,
                                current_files_in_snippet_dir_for_readme)
        run_git_command(["git", "add", os.path.join(snippet_folder_name, "README.md")], cwd=local_repo_path)
        status_output_readme, _ = run_git_command(["git", "status", "--porcelain", os.path.join(snippet_folder_name, "README.md")],
                                                  cwd=local_repo_path)

        # Only commit if the README has changed or is new
        if status_output_readme and status_output_readme.strip():
            readme_commit_env = {
                "GIT_AUTHOR_NAME": args.committer_name,
                "GIT_AUTHOR_EMAIL": args.committer_email,
                "GIT_AUTHOR_DATE": datetime.now(timezone.utc).isoformat(),  # MODIFIED
                "GIT_COMMITTER_NAME": args.committer_name,
                "GIT_COMMITTER_EMAIL": args.committer_email,
                "GIT_COMMITTER_DATE": datetime.now(timezone.utc).isoformat()  # MODIFIED
            }
            run_git_command(["git", "commit", "-m", f"Update README for snippet: {snippet_title} (ID: {snippet_id})"],
                            cwd=local_repo_path, env_vars=readme_commit_env)

        all_backed_up_snippets_info.append({'id': snippet_id, 'title': snippet_title, 'dir_name': snippet_folder_name})

    generate_root_readme(local_repo_path, all_backed_up_snippets_info)
    run_git_command(["git", "add", "README.md"], cwd=local_repo_path)
    status_output_root_readme, _ = run_git_command(["git", "status", "--porcelain", "README.md"], cwd=local_repo_path)
    if status_output_root_readme and status_output_root_readme.strip():
        root_readme_commit_env = {
            "GIT_AUTHOR_NAME": args.committer_name,
            "GIT_AUTHOR_EMAIL": args.committer_email,
            "GIT_AUTHOR_DATE": datetime.now(timezone.utc).isoformat(),
            "GIT_COMMITTER_NAME": args.committer_name,
            "GIT_COMMITTER_EMAIL": args.committer_email,
            "GIT_COMMITTER_DATE": datetime.now(timezone.utc).isoformat()
        }
        run_git_command(["git", "commit", "-m", "Update root README with snippet index"],
                        cwd=local_repo_path, env_vars=root_readme_commit_env)

    print("\nSnippet backup process complete.")
    print(f"All snippets and their revisions should be in: {os.path.abspath(local_repo_path)}")
    print("You can now inspect the git log in that directory.")


if __name__ == "__main__":
    main()
