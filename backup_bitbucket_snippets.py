import argparse
import json
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
                    return result
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
                                current_delay = wait_time
                            except ValueError:
                                print(
                                    f"    Could not parse Retry-After header ('{retry_after_header}'). Using exponential backoff.")
                                current_delay = initial_delay * (backoff_factor ** (attempts - 1))
                        else:
                            current_delay = initial_delay * (backoff_factor ** (attempts - 1))
                    elif status_code is None:
                        should_retry = True
                        current_delay = initial_delay * (backoff_factor ** (attempts - 1))

                    if not should_retry:
                        print(f"    Non-retryable HTTP error for {func.__name__}. Status: {status_code if status_code else 'N/A'}")
                        if status_code == 404 and func.__name__ in ['fetch_from_bitbucket_paginated',
                                                                    'fetch_single_from_bitbucket', 'fetch_file_content']:
                            return None
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
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON from {url}: {e}\nResponse text: {response.text[:500]}...")
            return None

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
    try:
        return response.json()
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from {url}: {e}\nResponse text: {response.text[:500]}...")
        return None


@retry_api_call()
def fetch_file_content(url, auth_user, auth_password):
    print(f"Fetching file: {url}")
    response = requests.get(url, auth=(auth_user, auth_password))
    response.raise_for_status()
    return response.content


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
            is_nothing_to_commit_msg = "nothing to commit" in stderr.lower() or \
                                       "no changes added to commit" in stderr.lower() or \
                                       "working tree clean" in stderr.lower()
            is_allow_empty_variant = command_list[0:2] == ["git", "commit"] and \
                                     any(arg in command_list for arg in ["--allow-empty", "--allow-empty-message"])

            if not (is_allow_empty_variant and is_nothing_to_commit_msg):
                print(f"  Error running Git command: {' '.join(command_list)}")
                if stdout and stdout.strip(): print(f"  Stdout: {stdout.strip()}")
                if stderr and stderr.strip(): print(f"  Stderr: {stderr.strip()}")
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
    os.makedirs(os.path.dirname(readme_path), exist_ok=True)
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
    parser = argparse.ArgumentParser(description="Backup BitBucket Snippets to a local Git repository.",
                                     formatter_class=argparse.RawTextHelpFormatter)
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

    all_snippets_info_for_readme = []
    all_pending_commits_data = []

    print("\n--- Phase 1: Aggregating all snippet revision information ---")
    snippets_to_process = []
    if args.snippet_ids:
        ids_list = [s_id.strip() for s_id in args.snippet_ids.split(',')]
        print(f"Fetching specific snippet IDs: {ids_list} from workspace {args.workspace}")
        for s_id in ids_list:
            snippet_detail_endpoint = f"/snippets/{args.workspace}/{s_id}"
            snippet_detail = fetch_single_from_bitbucket(snippet_detail_endpoint, args.auth_user, args.auth_pass, args.api_base_url)
            if snippet_detail and snippet_detail.get('type') == 'snippet':
                snippets_to_process.append(snippet_detail)
            else:
                print(f"Could not fetch details for snippet ID: {s_id} in workspace {args.workspace}. Response: {snippet_detail}")
    else:
        snippets_list_endpoint = f"/snippets/{args.workspace}"
        if args.role: snippets_list_endpoint += f"?role={args.role}"
        print(f"Fetching snippets from workspace: {args.workspace} ...")
        snippets_data = fetch_from_bitbucket_paginated(snippets_list_endpoint, args.auth_user, args.auth_pass, args.api_base_url)
        if snippets_data and "values" in snippets_data:
            snippets_to_process = snippets_data["values"]

    if not snippets_to_process:
        print("No snippets found or specified to process.")
        generate_root_readme(local_repo_path, all_snippets_info_for_readme)
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

        all_snippets_info_for_readme.append({
            'id': snippet_id, 'title': snippet_title,
            'dir_name': snippet_folder_name, 'html_link': bitbucket_html_link
        })

        if args.historical:
            commits_endpoint = f"/snippets/{workspace_slug_for_snippet_api}/{snippet_id}/commits"
            print(f"  Fetching commits for snippet {snippet_id}...")
            commits_data = fetch_from_bitbucket_paginated(commits_endpoint, args.auth_user, args.auth_pass, args.api_base_url)
            if commits_data and commits_data.get("values"):
                for commit_details_raw in commits_data["values"]:
                    all_pending_commits_data.append({
                        "snippet_id": snippet_id, "snippet_title": snippet_title,
                        "snippet_folder_name": snippet_folder_name,
                        "workspace_slug_for_snippet_api": workspace_slug_for_snippet_api,  # Storing for Phase 3
                        "commit_sha": commit_details_raw["hash"], "commit_date_str": commit_details_raw["date"],
                        "commit_author_obj": commit_details_raw.get("author", {}),
                        "commit_message_summary": commit_details_raw.get("message", f"Revision {commit_details_raw['hash'][:7]}"),
                        "source_data_for_files_override": None
                    })
            elif args.historical:  # If --historical but no commits found
                print(
                    f"    No explicit commit history for '{snippet_title}' (ID: {snippet_id}), but --historical was set. Will attempt to back up current state as single revision.")

        # Logic to add the "latest state" if not doing historical OR if historical failed to find commits
        needs_latest_state_processing = not args.historical
        if args.historical and not any(pc['snippet_id'] == snippet_id and pc.get("source_data_for_files_override") is None for pc in
                                       all_pending_commits_data):
            needs_latest_state_processing = True  # If historical, but no actual history was added, process latest

        if needs_latest_state_processing:
            print(f"    Processing latest state for snippet '{snippet_title}' (ID: {snippet_id}).")
            full_snippet_detail = fetch_single_from_bitbucket(f"/snippets/{workspace_slug_for_snippet_api}/{snippet_id}",
                                                              args.auth_user, args.auth_pass, args.api_base_url)
            if not full_snippet_detail:
                print(f"    Failed to fetch full details for snippet {snippet_id}. Skipping this snippet.")
                all_snippets_info_for_readme = [s for s in all_snippets_info_for_readme if s['id'] != snippet_id]
                continue

            actual_head_sha = snippet_id
            if full_snippet_detail.get("files"):
                file_keys = list(full_snippet_detail.get("files", {}).keys())
                if file_keys:
                    first_file_name = file_keys[0]
                    file_meta = full_snippet_detail.get("files", {}).get(first_file_name, {})
                    file_self_link = file_meta.get("links", {}).get("self", {}).get("href", "")
                    match = re.search(r'/snippets/[^/]+/[^/]+/([^/]+)/files/', file_self_link)
                    if match: actual_head_sha = match.group(1)

            latest_date = full_snippet_detail.get("updated_on",
                                                  full_snippet_detail.get("created_on", datetime.now(timezone.utc).isoformat()))
            owner_info = full_snippet_detail.get("owner", snippet_info.get("owner", {}))
            author_name = owner_info.get("nickname", owner_info.get("display_name", args.committer_name))

            all_pending_commits_data.append({
                "snippet_id": snippet_id, "snippet_title": snippet_title,
                "snippet_folder_name": snippet_folder_name,
                "workspace_slug_for_snippet_api": workspace_slug_for_snippet_api,  # Storing for Phase 3
                "commit_sha": actual_head_sha,
                "commit_display_sha": actual_head_sha,
                "commit_date_str": latest_date,
                "commit_author_obj": {"raw": f"{author_name} <placeholder@example.com>", "nickname": author_name,
                                      "display_name": author_name},
                "commit_message_summary": f"Latest state of snippet '{snippet_title}'",
                "source_data_for_files_override": full_snippet_detail
            })

    if not all_pending_commits_data:
        print("No revisions to process across all snippets after aggregation.")
        generate_root_readme(local_repo_path, all_snippets_info_for_readme)
        return

    print("\n--- Phase 2: Sorting all revisions globally by date ---")
    try:
        for pc_item in all_pending_commits_data:
            date_str = pc_item["commit_date_str"]
            if not isinstance(date_str, str): date_str = datetime.now(timezone.utc).isoformat()
            pc_item["commit_date_obj"] = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        sorted_global_commits = sorted(all_pending_commits_data, key=lambda x: x['commit_date_obj'])
    except (ValueError, TypeError) as e:
        print(f"Error converting commit dates for sorting: {e}. Aborting.")
        return

    print(f"Total revisions to process chronologically: {len(sorted_global_commits)}")

    print("\n--- Phase 3: Processing and committing revisions chronologically ---")
    snippet_latest_file_lists = {info['id']: set() for info in all_snippets_info_for_readme}

    for pending_commit_data in sorted_global_commits:
        snippet_id = pending_commit_data["snippet_id"]
        snippet_title = pending_commit_data["snippet_title"]
        snippet_folder_name = pending_commit_data["snippet_folder_name"]
        # Use the consistent variable name from aggregation phase
        workspace_slug_for_snippet_api = pending_commit_data["workspace_slug_for_snippet_api"]
        commit_sha = pending_commit_data["commit_sha"]
        commit_display_sha_for_log = pending_commit_data.get("commit_display_sha", commit_sha)

        commit_date_for_git = pending_commit_data["commit_date_obj"].isoformat()
        commit_author_obj = pending_commit_data["commit_author_obj"]
        commit_message_summary = pending_commit_data["commit_message_summary"].replace('"', "'").replace('\n', ' ')

        commit_author_name = args.committer_name
        commit_author_email = args.committer_email
        raw_author_str = commit_author_obj.get("raw")

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

        print(
            f"    Processing Snippet '{snippet_title}' (ID: {snippet_id}) Revision: {commit_display_sha_for_log[:12]} dated {commit_date_for_git}")

        snippet_files_base_dir = os.path.join(local_repo_path, snippet_folder_name)
        os.makedirs(snippet_files_base_dir, exist_ok=True)

        revision_data = pending_commit_data.get("source_data_for_files_override")
        if not revision_data:
            revision_details_endpoint = f"/snippets/{workspace_slug_for_snippet_api}/{snippet_id}/{commit_sha}"
            revision_data = fetch_single_from_bitbucket(revision_details_endpoint, args.auth_user, args.auth_pass,
                                                        args.api_base_url)

        files_in_this_commit_relative_paths = set()
        if not revision_data or "files" not in revision_data or not isinstance(revision_data.get("files"), dict):
            print(
                f"      Could not get valid file list for snippet '{snippet_title}' revision {commit_sha}. Files data: {str(revision_data.get('files'))[:200] if revision_data else 'No revision data'}...")
            # files_in_this_commit_relative_paths remains empty
        else:
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

        snippet_latest_file_lists[snippet_id] = files_in_this_commit_relative_paths

        tracked_files_stdout, _ = run_git_command(["git", "ls-files", snippet_folder_name], cwd=local_repo_path)
        if tracked_files_stdout is not None:
            current_git_files_for_snippet_dir = set()
            if tracked_files_stdout.strip():
                for line in tracked_files_stdout.strip().split('\n'):
                    if line.startswith(snippet_folder_name + os.path.sep):
                        current_git_files_for_snippet_dir.add(line[len(snippet_folder_name) + 1:].replace(os.path.sep, "/"))

            for git_file_rel_to_snippet_dir in current_git_files_for_snippet_dir:
                if git_file_rel_to_snippet_dir not in files_in_this_commit_relative_paths and git_file_rel_to_snippet_dir != "README.md":
                    file_to_remove_in_repo = os.path.join(snippet_folder_name, git_file_rel_to_snippet_dir).replace(os.path.sep,
                                                                                                                    "/")
                    print(
                        f"      Marking for deletion (not in current snippet revision): {file_to_remove_in_repo.replace(snippet_folder_name + '/', '')}")
                    run_git_command(["git", "rm", file_to_remove_in_repo], cwd=local_repo_path)

        run_git_command(["git", "add", snippet_folder_name], cwd=local_repo_path)

        commit_env = {
            "GIT_AUTHOR_NAME": commit_author_name,
            "GIT_AUTHOR_EMAIL": commit_author_email,
            "GIT_AUTHOR_DATE": commit_date_for_git,
            "GIT_COMMITTER_NAME": args.committer_name,
            "GIT_COMMITTER_EMAIL": args.committer_email,
            "GIT_COMMITTER_DATE": commit_date_for_git
        }

        final_commit_message = f"Snippet: {snippet_title} (ID: {snippet_id})\nRev: {commit_display_sha_for_log[:7]}\n\n{commit_message_summary}"

        status_output, _ = run_git_command(["git", "status", "--porcelain", snippet_folder_name], cwd=local_repo_path)
        if status_output and status_output.strip():
            print(f"      Committing revision {commit_display_sha_for_log[:7]} with date {commit_date_for_git}...")
            run_git_command(["git", "commit", "--allow-empty", "-m", final_commit_message],
                            cwd=local_repo_path, env_vars=commit_env)
        elif args.historical:
            print(f"    No file changes for historical revision {commit_display_sha_for_log[:7]}, making empty marker commit.")
            run_git_command(["git", "commit", "--allow-empty", "--allow-empty-message", "-m", final_commit_message],
                            cwd=local_repo_path, env_vars=commit_env)
        elif not args.historical and not (status_output and status_output.strip()):
            print(f"      No file changes to commit for revision {commit_display_sha_for_log[:7]}.")

    print("\n--- Phase 4: Generating and Committing README files ---")
    for snippet_readme_info in all_snippets_info_for_readme:
        s_id = snippet_readme_info['id']
        s_title = snippet_readme_info['title']
        s_folder_name = snippet_readme_info['dir_name']
        s_html_link = snippet_readme_info['html_link']
        s_files = snippet_latest_file_lists.get(s_id, set())

        s_base_dir = os.path.join(local_repo_path, s_folder_name)
        generate_snippet_readme(s_base_dir, s_title, s_id, s_html_link, s_files)

        readme_git_path = os.path.join(s_folder_name, "README.md")
        run_git_command(["git", "add", readme_git_path], cwd=local_repo_path)

        status_readme, _ = run_git_command(["git", "status", "--porcelain", readme_git_path], cwd=local_repo_path)
        if status_readme and status_readme.strip():
            readme_commit_env = {
                "GIT_AUTHOR_NAME": args.committer_name,
                "GIT_AUTHOR_EMAIL": args.committer_email,
                "GIT_AUTHOR_DATE": datetime.now(timezone.utc).isoformat(),
                "GIT_COMMITTER_NAME": args.committer_name,
                "GIT_COMMITTER_EMAIL": args.committer_email,
                "GIT_COMMITTER_DATE": datetime.now(timezone.utc).isoformat()
            }
            run_git_command(["git", "commit", "-m", f"Update README for snippet: {s_title} (ID: {s_id})"],
                            cwd=local_repo_path, env_vars=readme_commit_env)

    generate_root_readme(local_repo_path, all_snippets_info_for_readme)
    run_git_command(["git", "add", "README.md"], cwd=local_repo_path)
    status_root_readme, _ = run_git_command(["git", "status", "--porcelain", "README.md"], cwd=local_repo_path)
    if status_root_readme and status_root_readme.strip():
        root_readme_env = {
            "GIT_AUTHOR_NAME": args.committer_name,
            "GIT_AUTHOR_EMAIL": args.committer_email,
            "GIT_AUTHOR_DATE": datetime.now(timezone.utc).isoformat(),
            "GIT_COMMITTER_NAME": args.committer_name,
            "GIT_COMMITTER_EMAIL": args.committer_email,
            "GIT_COMMITTER_DATE": datetime.now(timezone.utc).isoformat()
        }
        run_git_command(["git", "commit", "-m", "Update root README with snippet index"],
                        cwd=local_repo_path, env_vars=root_readme_env)

    print("\nSnippet backup process complete.")
    print(f"All snippets and their revisions should be in: {os.path.abspath(local_repo_path)}")
    print("You can now inspect the git log in that directory. It should be globally chronological.")


if __name__ == "__main__":
    main()
