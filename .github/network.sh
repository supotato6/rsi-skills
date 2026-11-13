#!/usr/bin/env bash
set -euo pipefail

# Probe the actual 11 KB locked wheel URL: the index can work while wheel TLS fails.
# Hosted runners use their normal network; this host has an explicit policy.
rsi_ci_policy=${RSI_NETWORK_ENV:-/mnt/disk2/guohongjie_home/.config/network/proxy.env}
if [[ -r "$rsi_ci_policy" ]]; then
  echo "Dependency network policy: configured"
  source "$rsi_ci_policy"
  export PROXY_PROBE_URL=${PROXY_PROBE_URL:-https://pypi.tuna.tsinghua.edu.cn/packages/b7/ce/149a00dd41f10bc29e5921b496af8b574d8413afcd5e30dfa0ed46c2cc5e/six-1.17.0-py2.py3-none-any.whl}
  proxy_run "$@"
elif [[ ${https_proxy:-${HTTPS_PROXY:-}} == http://10.0.2.2:7890 || ${https_proxy:-${HTTPS_PROXY:-}} == http://127.0.0.1:7890 ]]; then
  # The runner VM maps the host's default proxy through its existing gateway.
  # The host policy file is not mounted there. Keep the same ordered fallbacks.
  # Probe with the installer TLS client too. curl can succeed while uv fails.
  # This unpacks one hash-locked 11 KB public wheel into owned scratch space;
  # it never executes package code or replays the real installer.
  probe_package_client() (
    local scratch result
    scratch=$(mktemp -d) || return 2
    trap 'rm -rf -- "$scratch"' EXIT
    case "${1##*/}" in
      uv)
        if timeout 30 "$1" pip install --no-cache --no-deps --target "$scratch/package" \
            'https://pypi.tuna.tsinghua.edu.cn/packages/b7/ce/149a00dd41f10bc29e5921b496af8b574d8413afcd5e30dfa0ed46c2cc5e/six-1.17.0-py2.py3-none-any.whl#sha256=4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274' \
            >"$scratch/log" 2>&1; then return 0; else result=$?; fi ;;
      python|python[0-9]*)
        if timeout 30 "$1" -m pip download --timeout 10 --retries 0 --no-cache-dir --no-index --disable-pip-version-check --no-deps \
            --only-binary=:all: --dest "$scratch/package" \
            'https://pypi.tuna.tsinghua.edu.cn/packages/b7/ce/149a00dd41f10bc29e5921b496af8b574d8413afcd5e30dfa0ed46c2cc5e/six-1.17.0-py2.py3-none-any.whl#sha256=4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274' \
            >"$scratch/log" 2>&1; then return 0; else result=$?; fi ;;
      *) return 0 ;;
    esac
    echo "Package-client probe failed (exit=$result)"
    tail -n 6 "$scratch/log"
    # Permission/quota errors and unavailable clients are not route failures.
    if [[ $result == 127 ]] || grep -Eq '\b(401|403|404|429)\b|No module named pip' "$scratch/log"; then
      return 2
    fi
    return 1
  )
  proxy_run() {
    local endpoint status result index=0
    for endpoint in "${https_proxy:-$HTTPS_PROXY}" http://172.28.4.16:18999 http://172.27.128.224:8234; do
      index=$((index+1))
      if result=$(curl --silent --fail --location --output /dev/null --write-out '%{http_code}' \
          --connect-timeout 10 --max-time 15 --retry 0 --noproxy '' --proxy "$endpoint" \
          "${PROXY_PROBE_URL:-https://pypi.tuna.tsinghua.edu.cn/packages/b7/ce/149a00dd41f10bc29e5921b496af8b574d8413afcd5e30dfa0ed46c2cc5e/six-1.17.0-py2.py3-none-any.whl}"); then
        if (export http_proxy="$endpoint" HTTP_PROXY="$endpoint" https_proxy="$endpoint" HTTPS_PROXY="$endpoint" \
            all_proxy="$endpoint" ALL_PROXY="$endpoint" ftp_proxy="$endpoint" FTP_PROXY="$endpoint"; probe_package_client "$@"); then
          echo "Dependency network policy: VM route $index (package client verified)"
          # exec guarantees a failed installer is never replayed on another route.
          exec env http_proxy="$endpoint" HTTP_PROXY="$endpoint" https_proxy="$endpoint" HTTPS_PROXY="$endpoint" \
            all_proxy="$endpoint" ALL_PROXY="$endpoint" ftp_proxy="$endpoint" FTP_PROXY="$endpoint" "$@"
        else
          status=$?
          if [[ $status == 2 ]]; then return 1; fi
          echo "Dependency package-client probe $index failed; installer was not started"
        fi
      else
        status=$?
        echo "Dependency proxy probe $index failed: curl=$status HTTP=$result"
        if [[ $result =~ ^(401|403|404|429)$ ]]; then return 1; fi
      fi
    done
    echo 'No configured CI proxy route passed its safe GET probe' >&2
    return 1
  }
  proxy_run "$@"
else
  echo "Dependency network policy: runner default"
  "$@"
fi
