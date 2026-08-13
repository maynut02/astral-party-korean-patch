#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$ROOT/.work/steam-token-bootstrap"
DOTNET_ROOT="$ROOT/.cache/dotnet"
TOKEN_DIR="$ROOT/.secrets"
SOURCE="$WORK/DepotDownloader"
COMMIT="c553ef4d60c00a4f5fd16c9fe017f569001589ff"

mkdir -p "$WORK" "$DOTNET_ROOT" "$TOKEN_DIR"
chmod 700 "$TOKEN_DIR"

if [ ! -x "$DOTNET_ROOT/dotnet" ]; then
  curl -fsSL https://dot.net/v1/dotnet-install.sh -o "$WORK/dotnet-install.sh"
  bash "$WORK/dotnet-install.sh" --channel 9.0 --install-dir "$DOTNET_ROOT" --no-path
fi
export DOTNET_ROOT
export PATH="$DOTNET_ROOT:$PATH"

DOTNET_PROBE="$WORK/dotnet-probe.log"
if ! dotnet --info >"$DOTNET_PROBE" 2>&1; then
  cat "$DOTNET_PROBE" >&2
  if grep -qi 'ICU' "$DOTNET_PROBE"; then
    printf '%s\n' 'WSL의 .NET 실행에 필요한 ICU 런타임을 사용할 수 없습니다.' >&2
    if command -v apt-get >/dev/null 2>&1; then
      printf '%s\n' '다음 명령으로 ICU를 설치/갱신한 뒤 다시 실행하세요:' >&2
      printf '%s\n' '  sudo apt-get update && sudo apt-get install -y libicu-dev' >&2
      printf '%s\n' '설치 후에도 실패하면 다음 진단 결과를 확인하세요:' >&2
      printf '%s\n' "  dpkg -l | grep -E 'libicu|icu-dev'" >&2
      printf '%s\n' '  ldconfig -p | grep -i libicu' >&2
    fi
  else
    printf '%s\n' '.NET 런타임 사전 검사가 실패했습니다. 위 오류를 확인하세요.' >&2
  fi
  exit 1
fi
rm -f "$DOTNET_PROBE"

rm -rf "$SOURCE"
git clone --filter=blob:none https://github.com/SteamRE/DepotDownloader.git "$SOURCE"
git -C "$SOURCE" checkout --detach "$COMMIT"
git -C "$SOURCE" apply "$ROOT/tools/depotdownloader-ci.patch"

dotnet build "$SOURCE/DepotDownloader/DepotDownloader.csproj" -c Release --nologo

TOKEN_FILE="$TOKEN_DIR/steam-refresh-token.txt"
rm -f "$TOKEN_FILE"
export DEPOTDOWNLOADER_TOKEN_OUTPUT="$TOKEN_FILE"

printf '%s\n' 'Steam 모바일 앱으로 표시되는 QR 코드를 승인하세요.'
set +e
dotnet run --project "$SOURCE/DepotDownloader/DepotDownloader.csproj" -c Release --no-build -- \
  -app 2622000 \
  -depot 2622001 \
  -qr \
  -remember-password \
  -manifest-only \
  -os windows \
  -dir "$WORK/steam-download"
status=$?
set -e

if [ ! -s "$TOKEN_FILE" ]; then
  printf 'Steam refresh token을 생성하지 못했습니다. DepotDownloader 종료 코드: %s\n' "$status" >&2
  exit 1
fi
chmod 600 "$TOKEN_FILE"
printf '\n토큰을 생성했습니다: %s\n' "$TOKEN_FILE"
printf '%s\n' '이 파일은 Git에 포함되지 않습니다. 내용을 채팅에 붙여넣지 마세요.'
printf '%s\n' 'GitHub 저장소를 연결한 뒤 다음처럼 Secret으로 등록할 수 있습니다:'
printf '  gh secret set STEAM_REFRESH_TOKEN < %q\n' "$TOKEN_FILE"
printf '%s\n' 'STEAM_USERNAME도 별도 Secret으로 등록해야 합니다.'
