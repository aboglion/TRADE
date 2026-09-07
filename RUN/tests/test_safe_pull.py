import subprocess
from pathlib import Path


def test_safe_pull_script_preserves_local_changes(tmp_path: Path):
    """
    Test that safe_pull.sh stashes local modifications and untracked files
    prior to git pull, creating backups and preventing merge aborts.
    """
    repo_dir = tmp_path / "repo"
    clone_dir = tmp_path / "clone"
    repo_dir.mkdir()

    # 1. Initialize origin git repository
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True)

    test_file = repo_dir / "version.txt"
    test_file.write_text("v1.0.0\n")
    subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(repo_dir), check=True)

    # 2. Clone repository to simulate production/local environment
    subprocess.run(["git", "clone", str(repo_dir), str(clone_dir)], check=True)
    subprocess.run(["git", "config", "user.name", "LocalUser"], cwd=str(clone_dir), check=True)
    subprocess.run(["git", "config", "user.email", "local@example.com"], cwd=str(clone_dir), check=True)

    # Copy safe_pull.sh into clone
    safe_pull_src = Path(__file__).parent.parent / "scripts" / "safe_pull.sh"
    scripts_dir = clone_dir / "RUN" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    clone_script = scripts_dir / "safe_pull.sh"
    clone_script.write_text(safe_pull_src.read_text())
    clone_script.chmod(0o755)

    # Commit the script in origin so both branches have it
    subprocess.run(["cp", str(safe_pull_src), str(repo_dir / "safe_pull.sh")], check=True)
    subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "commit", "-m", "Add script"], cwd=str(repo_dir), check=True)

    # 3. Create local modifications and untracked files in clone
    local_modified = clone_dir / "local_settings.txt"
    local_modified.write_text("my_custom_local_setting=123\n")

    # 4. Push remote change from origin
    (repo_dir / "version.txt").write_text("v1.1.0\n")
    subprocess.run(["git", "commit", "-am", "Bump version remotely"], cwd=str(repo_dir), check=True)

    # 5. Run safe_pull.sh in clone
    res = subprocess.run(
        [str(clone_script), "main"],
        cwd=str(clone_dir),
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"safe_pull.sh failed: {res.stdout}\n{res.stderr}"

    # Verify pull succeeded and version updated
    assert (clone_dir / "version.txt").read_text() == "v1.1.0\n"

    # Verify local untracked/modified file was preserved
    assert local_modified.exists()
    assert local_modified.read_text() == "my_custom_local_setting=123\n"

    # Verify backup folder was created
    backup_root = clone_dir / "backups" / "local_changes"
    assert backup_root.exists()
    assert any(backup_root.iterdir()), "A timestamped backup folder should exist"


def test_safe_pull_handles_conflicts_and_protects_stash(tmp_path: Path):
    """
    Test that when both remote and local edit the same file line (merge conflict),
    safe_pull.sh pulls the code, detects the conflict upon unstash, keeps the working
    tree clean (so bot doesn't crash on syntax errors), while preserving the local
    changes in git stash and the backup folder.
    """
    repo_dir = tmp_path / "repo"
    clone_dir = tmp_path / "clone"
    repo_dir.mkdir()

    # 1. Initialize origin
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True)

    test_file = repo_dir / "config.txt"
    test_file.write_text("initial_config=true\n")
    subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(repo_dir), check=True)

    # 2. Clone
    subprocess.run(["git", "clone", str(repo_dir), str(clone_dir)], check=True)
    subprocess.run(["git", "config", "user.name", "LocalUser"], cwd=str(clone_dir), check=True)
    subprocess.run(["git", "config", "user.email", "local@example.com"], cwd=str(clone_dir), check=True)

    # Copy script into clone
    safe_pull_src = Path(__file__).parent.parent / "scripts" / "safe_pull.sh"
    scripts_dir = clone_dir / "RUN" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    clone_script = scripts_dir / "safe_pull.sh"
    clone_script.write_text(safe_pull_src.read_text())
    clone_script.chmod(0o755)

    # 3. Local modification to config.txt
    (clone_dir / "config.txt").write_text("initial_config=false_local\n")

    # 4. Remote conflicting modification to config.txt
    (repo_dir / "config.txt").write_text("initial_config=remote_updated\n")
    subprocess.run(["git", "commit", "-am", "Remote update to config.txt"], cwd=str(repo_dir), check=True)

    # 5. Run safe_pull.sh
    res = subprocess.run(
        [str(clone_script), "main"],
        cwd=str(clone_dir),
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0

    # Clean working tree at latest commit (no syntax error / conflict markers)
    content = (clone_dir / "config.txt").read_text()
    assert "<<<<<<<" not in content
    assert "initial_config=remote_updated" in content

    # Verify local changes are preserved in git stash
    stash_res = subprocess.run(["git", "stash", "list"], cwd=str(clone_dir), capture_output=True, text=True)
    assert "Auto-saved local changes before git pull" in stash_res.stdout

    # Verify local changes are also saved in backup directory
    backup_root = clone_dir / "backups" / "local_changes"
    assert backup_root.exists()

