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
