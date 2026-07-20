#!/usr/bin/env bash
# 把 GitHub 最新 Release 的 exe 同步到门户下载目录，并生成应用内更新源 version.json。
# 幂等：版本没变直接退出。由 cron 定时执行（也可手动跑）。
#   crontab: */20 * * * * flock -n /tmp/ivyea-translate-sync.lock /root/ivyea-translate/deploy/sync-release.sh >> /var/log/ivyea-translate-sync.log 2>&1
set -euo pipefail

REPO="Hector-xue/ivyea-translate"
DEST="/var/www/translate.ivyea.com/download"
BASE_URL="https://translate.ivyea.com/download"

mkdir -p "$DEST"

meta=$(curl -fsSL --max-time 30 "https://api.github.com/repos/$REPO/releases/latest")
tag=$(echo "$meta" | python3 -c "import json,sys; print(json.load(sys.stdin)['tag_name'])")
version="${tag#v}"

current=""
if [ -f "$DEST/version.json" ]; then
    current=$(python3 -c "import json; print(json.load(open('$DEST/version.json'))['version'])" 2>/dev/null || true)
fi
if [ "$current" = "$version" ]; then
    exit 0
fi
echo "$(date '+%F %T') 同步 $tag（当前 $current）"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

echo "$meta" | python3 -c "
import json, sys
for a in json.load(sys.stdin)['assets']:
    print(a['name'] + '\t' + a['browser_download_url'])
" | while IFS=$'\t' read -r name url; do
    case "$name" in
        IvyeaTranslate.exe|IvyeaTranslate-Setup.exe|IvyeaTranslate-mac-arm64.dmg)
            echo "  下载 $name"
            curl -fsSL --max-time 600 -o "$tmp/$name" "$url"
            ;;
    esac
done

[ -f "$tmp/IvyeaTranslate-Setup.exe" ] || { echo "缺 Setup.exe，放弃"; exit 1; }
[ -f "$tmp/IvyeaTranslate.exe" ] || { echo "缺便携版 exe，放弃"; exit 1; }

mv -f "$tmp/IvyeaTranslate-Setup.exe" "$DEST/IvyeaTranslate-Setup.exe"
mv -f "$tmp/IvyeaTranslate.exe" "$DEST/IvyeaTranslate.exe"
# macOS 是未签名 Beta，可能某个版本没出产物 -> 有才同步，缺了不算失败
mac_url=""
if [ -f "$tmp/IvyeaTranslate-mac-arm64.dmg" ]; then
    mv -f "$tmp/IvyeaTranslate-mac-arm64.dmg" "$DEST/IvyeaTranslate-mac-arm64.dmg"
    mac_url="$BASE_URL/IvyeaTranslate-mac-arm64.dmg"
fi

echo "$meta" | python3 -c "
import json, sys
m = json.load(sys.stdin)
out = {
    'version': m['tag_name'].lstrip('v'),
    'tag': m['tag_name'],
    'published_at': m.get('published_at', ''),
    'notes': (m.get('body') or '')[:2000],
    'setup_url': '$BASE_URL/IvyeaTranslate-Setup.exe',
    'portable_url': '$BASE_URL/IvyeaTranslate.exe',
    'page_url': 'https://translate.ivyea.com/',
    'mac_url': '$mac_url',  # 未签名 Beta；空串=该版本没出 mac 产物
}
json.dump(out, open('$DEST/version.json.tmp', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
"
mv -f "$DEST/version.json.tmp" "$DEST/version.json"
echo "$(date '+%F %T') 完成：$version"
