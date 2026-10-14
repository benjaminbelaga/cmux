#!/usr/bin/env bash
# Local terminal use is a signed-out product mode, not an auth fixture.
cmux_yoyaku_local_launch_policy() {
  if [[ "$1" != yoyaku-lordsquire || "$2" != Release ]]; then
    echo 'error: --local-only requires --tag yoyaku-lordsquire --release' >&2
    return 2
  fi
  if [[ "$3" != 0 || -n "$4$5$6" ]]; then
    echo 'error: --local-only cannot use auth profiles or credentials' >&2
    return 2
  fi
  if [[ "$7" != 'CMUX YOYAKU' || "$8" != 0 ]]; then
    echo 'error: --local-only requires the CMUX YOYAKU name and no bundle-id override' >&2
    return 2
  fi
  # Never carry development auto-login or UI-test fixtures into the canary.
  unset CMUX_DOGFOOD_STACK_EMAIL CMUX_DOGFOOD_STACK_PASSWORD
  unset CMUX_UITEST_STACK_EMAIL CMUX_UITEST_STACK_PASSWORD
  unset CMUX_UITEST_CLEAR_AUTH CMUX_UITEST_MOCK_AUTH CMUX_UI_TEST_MODE
  unset CMUX_DEV_AUTH_PROFILE CMUX_DEV_AUTH_ACCOUNT CMUX_AUTH_CREDENTIALS_FILE
  local yoyaku_launch_key
  while IFS= read -r yoyaku_launch_key; do
    case "$yoyaku_launch_key" in
      CMUX_UITEST_*|CMUX_UI_TEST_*|CMUX_TEST_*|CMUX_DOGFOOD_*) unset "$yoyaku_launch_key" ;;
    esac
  done < <(compgen -v)
}

# The fork archives upstream workflows. Artifact keys include their paths,
# although the client itself is unchanged. Bind to the frozen upstream source
# only after checking both runtime inputs and those workflow contents.
cmux_yoyaku_tui_source() {
  local root="$1" base name
  base="$(cat "$root/scripts/yoyaku/upstream-base")" || return
  git -C "$root" diff --quiet "$base" -- cmux-tui ghostty || {
    echo 'error: local cmux-tui inputs changed; build and supply a matching client' >&2
    return 2
  }
  for name in cmux-tui-artifacts.yml cmux-tui-build-package.yml; do
    git -C "$root" show "$base:.github/workflows/$name" | cmp -s - "$root/.github/upstream-workflows/$name" || {
      echo 'error: archived cmux-tui build inputs differ from the frozen upstream source' >&2
      return 2
    }
  done
  printf '%s\n' "$base"
}

cmux_yoyaku_socket_control_mode() {
  [[ "${1:-}" == 1 ]] || { echo "error: local canary requires local-only flag 1" >&2; return 2; }
  printf '%s\n' automation
}
