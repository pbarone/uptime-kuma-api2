"""
Check whether this library covers all monitor types and notification providers
that Uptime Kuma's server implements.

Compares the server source (cloned into a temp directory or a provided path)
against our MonitorType and NotificationType enums, and reports any gaps.

Exit codes:
    0 - successful run (gaps may or may not exist; check output)
    2 - script error (failed clone, import error, etc.)

Usage:
    python scripts/check_upstream_coverage.py [--upstream-path PATH]

    --upstream-path  Path to an existing Uptime Kuma checkout (skips cloning)

Output is ASCII only (Windows cp1252 safety).
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

from packaging.version import InvalidVersion
from packaging.version import parse as parse_version

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from uptime_kuma_api.monitor_type import MonitorType
from uptime_kuma_api.notification_providers import NotificationType


def extract_names_from_js(directory, prefer_type=False,
                          pattern=r'name\s*=\s*["\']([^"\']+)["\']'):
    """Extract type identifiers from all .js files in a directory.

    Monitor types and notification providers use different wire keys, so the
    caller must say which it wants:

    - Monitor types (``prefer_type=True``): the wire value is the ``type``
      property, so prefer ``type = "..."`` and fall back to ``name = "..."``.
    - Notification providers (``prefer_type=False``, the default): the wire
      value is *always* ``name = "..."``. These files also contain a local
      ``type`` variable in their send logic (e.g. ``type = "CRIT"`` in
      signalgrid.js as a severity flag), which is NOT a provider identifier,
      so ``type`` must never be consulted here.
    """
    names = set()
    if not os.path.isdir(directory):
        return names
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".js"):
            continue
        filepath = os.path.join(directory, filename)
        with open(filepath, encoding="utf-8", errors="replace") as f:
            content = f.read()

        if prefer_type:
            # Prefer `type = "..."` (the monitor wire value) if present.
            # The regex requires `type` at the start of a line (after optional
            # whitespace) to avoid matching inside SQL strings like
            # `WHERE type = 'certificate'` in globalping.js.
            type_match = re.search(r'^\s*type\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if type_match:
                names.add(type_match.group(1))
                continue

        # `name = "..."` (notification wire value, or monitor fallback)
        for match in re.finditer(pattern, content):
            value = match.group(1)
            # Skip obvious non-provider artifacts (URLs, protocols, etc.)
            if "://" in value or value == "notification-provider":
                continue
            names.add(value)
    return names


def get_our_enum_values(enum_class):
    """Get the set of string values from one of our str Enums."""
    return {member.value for member in enum_class}


def clone_upstream(target_dir):
    """Shallow-clone the Uptime Kuma repo to target_dir."""
    print("cloning louislam/uptime-kuma (shallow)...")
    result = subprocess.run(
        [
            "git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
            "https://github.com/louislam/uptime-kuma.git", target_dir,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"FAIL: git clone failed: {result.stderr.strip()}")
        return False

    # Sparse checkout only the directories we need
    result = subprocess.run(
        ["git", "sparse-checkout", "set", "server/notification-providers",
         "server/monitor-types", "package.json", "--skip-checks"],
        capture_output=True, text=True, cwd=target_dir,
    )
    if result.returncode != 0:
        print(f"FAIL: sparse-checkout failed: {result.stderr.strip()}")
        return False

    return True


def get_upstream_version(upstream_path):
    """Read the version from the upstream package.json."""
    pkg_path = os.path.join(upstream_path, "package.json")
    if not os.path.isfile(pkg_path):
        return "unknown"
    import json
    with open(pkg_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("version", "unknown")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-path", help="Path to existing Uptime Kuma checkout")
    args = parser.parse_args()

    # Determine upstream path
    cleanup_dir = None
    if args.upstream_path:
        upstream_path = args.upstream_path
        if not os.path.isdir(upstream_path):
            print(f"FAIL: --upstream-path does not exist: {upstream_path}")
            return 2
    else:
        cleanup_dir = tempfile.mkdtemp(prefix="upstream-kuma-")
        upstream_path = cleanup_dir
        if not clone_upstream(upstream_path):
            return 2

    try:
        version = get_upstream_version(upstream_path)
        print(f"upstream version: {version}")

        # Skip pre-release upstreams (betas, RCs). The default branch tracks
        # the next major while it is still a beta (e.g. 3.0.0-beta.0), whose
        # new monitor types / providers are not yet a stable contract to gate
        # against. Reporting them opens an issue for work we should not do yet.
        # When that version ships as a stable release, this guard stops firing
        # and the next scan opens the actionable issue. An unparseable version
        # is treated as non-pre-release so a real gap is never silently hidden.
        try:
            is_prerelease = parse_version(version).is_prerelease
        except InvalidVersion:
            is_prerelease = False
        if is_prerelease:
            print(f"\nupstream {version} is a pre-release - skipping gap check "
                  "(will re-check once a stable version ships)")
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                    f.write("has_gaps=false\n")
                    f.write(f"upstream_version={version}\n")
            return 0

        # Extract server-side names
        notification_dir = os.path.join(upstream_path, "server", "notification-providers")
        monitor_dir = os.path.join(upstream_path, "server", "monitor-types")

        server_notifications = extract_names_from_js(notification_dir)
        server_monitors = extract_names_from_js(monitor_dir, prefer_type=True)

        # Remove the base class file artifact if present
        server_notifications.discard("notification-provider")

        # Get our enum values
        our_notifications = get_our_enum_values(NotificationType)
        our_monitors = get_our_enum_values(MonitorType)

        # Find gaps
        missing_notifications = sorted(server_notifications - our_notifications)
        missing_monitors = sorted(server_monitors - our_monitors)

        # Report
        print(f"\nnotification providers: {len(our_notifications)} ours, "
              f"{len(server_notifications)} upstream")
        print(f"monitor types: {len(our_monitors)} ours, "
              f"{len(server_monitors)} upstream")

        has_gaps = False

        if missing_notifications:
            has_gaps = True
            print(f"\nMISSING notification providers ({len(missing_notifications)}):")
            for name in missing_notifications:
                print(f"  - {name}")

        if missing_monitors:
            has_gaps = True
            print(f"\nMISSING monitor types ({len(missing_monitors)}):")
            for name in missing_monitors:
                print(f"  - {name}")

        if not has_gaps:
            print("\nall covered - no gaps found")

        # Output for GitHub Actions
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write(f"has_gaps={'true' if has_gaps else 'false'}\n")
                f.write(f"upstream_version={version}\n")
                if missing_notifications:
                    f.write(f"missing_notifications={', '.join(missing_notifications)}\n")
                if missing_monitors:
                    f.write(f"missing_monitors={', '.join(missing_monitors)}\n")

        return 0

    finally:
        if cleanup_dir:
            import shutil
            shutil.rmtree(cleanup_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
