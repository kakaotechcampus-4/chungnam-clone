# 강의자료 최신화 — Claude 실행 지시서 (v2)

> **사용법 (3단계)**
>
> 1. **본인 clone repo 폴더에서 Claude Code 를 실행**합니다. (터미널에서 `cd <학교>-clone` → `claude`)
>    → git 명령이 이 폴더에서 돌아야 하므로 **가장 중요**합니다.
> 2. 다운로드한 **이 파일(`CLAUDE.md`)을 Claude Code 채팅창에 드래그**해서 넣습니다.
> 3. **"이 문서대로 강의자료 최신화 해줘"** 라고 입력하면 끝. Claude 가 내 git 상태 진단 → 안전 반영 → 검증까지 대신 해줍니다.
>
> ⚠️ 파일은 다운로드 폴더에 있어도 되지만, **Claude Code 자체는 반드시 내 clone repo 폴더(`<학교>-clone`)에서 켜야** 합니다.
> 다운로드 폴더 등 엉뚱한 곳에서 켜면 git 명령이 실패합니다 — 이 경우 Claude 가 감지해서 "clone repo 폴더에서 다시 켜세요" 라고 안내합니다.

---

## 0. Claude, 당신의 임무 (한 줄)

강사가 `main` 에 올린 **새 강의자료(새 주차·기본/심화 과제·프레임워크 수정)** 를 이 학생의 브랜치에 반영하되, **학생이 이미 작성한 코드는 절대 잃지 않게** 한다. 진단 → 조치 → 검증까지 수행하고, 애매하면 파괴적 명령 대신 학생에게 물어본다.

---

## 1. 문제 상황 (왜 그냥 pull 하면 안 되나)

강사 base code 는 `main` 에 계속 업데이트된다(새 주차, 그리고 **같은 주차의 심화 과제 추가** 등). 그런데 이 업데이트가 **학생이 이미 손댄 파일**을 건드리는 경우가 있다:

- (어제 사례) week02 공개 시 **week01 파일이 "모범답안"으로 재발행**됨 → 학생 구현과 충돌.
- (오늘 사례) **심화 과제**가 `student_parts/week02_*.py` **같은 파일에 TODO로 추가**될 수 있음 → 학생이 짜던 기본과제 구현과 같은 파일.

그래서 `git pull` / `git merge` 를 **아무렇게나** 하거나, `git checkout origin/main -- <파일>` 로 **덮어쓰면**:

- 충돌이 나거나,
- **학생이 짠 코드가 강사 파일(답안·stub)로 덮여 사라진다.** ⚠️

### 목표 상태(불변식) — 조치가 끝난 뒤 반드시 이래야 한다

- ✅ **내가 작성/구현한 코드는 전부 내 버전으로 보존** (모범답안·빈 stub 으로 덮이지 않음)
- ✅ **새 강의자료는 전부 반영** — 신규 파일, 프레임워크 수정, **기존 파일에 새로 추가된 과제(심화 TODO 등)** 포함

---

## 2. 먼저 확인 (Claude 가 실행)

```bash
# (0) 여기가 "내 clone repo 폴더"가 맞는지 먼저 확인 — 아니면 여기서 멈추고 학생에게 안내한다.
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "❌ 여기는 git 저장소가 아닙니다. 터미널에서 본인 clone repo 폴더(<학교>-clone)로 이동한 뒤 거기서 Claude Code 를 다시 켜세요."
  # → 이후 단계 실행하지 않는다.
fi
git remote -v | grep -q "kakaotechcampus-4/.*-clone" \
  && echo "✅ 학교 clone repo 확인됨" \
  || echo "⚠️ origin 이 'kakaotechcampus-4/<학교>-clone' 이 아님 — 올바른 폴더인지 학생에게 확인 후 진행"

# (1) 안전용 현재 위치 기록 (되돌릴 기준점 — 무엇도 잃지 않음)
git rev-parse HEAD

# (2) 내 이름 감지 (현재 브랜치에서 <이름> 추출, origin 에 그 final 있으면 신뢰)
CUR=$(git rev-parse --abbrev-ref HEAD)
NAME=${CUR%%/*}
if ! git rev-parse --verify -q "refs/heads/$NAME/final" >/dev/null \
   && ! git rev-parse --verify -q "refs/remotes/origin/$NAME/final" >/dev/null; then
  FINALS=$(git for-each-ref --format='%(refname:short)' refs/heads | grep '/final$')
  if [ "$(printf '%s\n' "$FINALS" | grep -c .)" = "1" ]; then NAME=$(printf '%s' "$FINALS" | sed 's#/final##'); else NAME=""; fi
fi
[ -n "$NAME" ] && echo "감지된 이름: $NAME" || echo "감지 실패 → 학생에게 '본인 브랜치 이름(<이름>/final 의 <이름>)'을 물어본다"

git fetch origin

# (3) 내 작업 브랜치들(복구·검증 소스) 목록
git branch -r --format='%(refname:short)' | grep "origin/$NAME/week"

# (4) 지금 상태 진단 → §4 case 판정
git status
git log --oneline -8
```

---

## 3. 핵심 원칙 (이 지시서의 심장 — Claude 는 이 원칙으로 판단한다)

이번 업데이트로 `main` 에서 들어오는 파일을 **자동으로 산출**하고, 각 파일을 **두 종류로 나눠서** 다르게 처리한다. (하드코딩된 파일 목록에 의존하지 않는다.)

```bash
# 내 작업 브랜치(아래 §4에서 정한 대상 브랜치)에 체크아웃한 상태에서:
MB=$(git merge-base HEAD origin/main)              # 공통 조상
echo "== main 에서 새로 들어오는 파일 (INCOMING) =="
git diff --name-only "$MB" origin/main
echo "== 내가 이미 수정한 파일 (I_CHANGED) =="
git diff --name-only "$MB" HEAD
```

- **SAFE 파일** = INCOMING 에는 있지만 내가 **안 건드린** 파일 (프레임워크 `fixed/`·`run.sh`, 완전 신규 파일 등)
  → **main 버전을 그대로 받아도 안전.** (`git checkout origin/main -- <파일>`)
- **MINE 파일** = INCOMING 이면서 내가 **수정한** 파일 (`student_parts/weekNN_*.py` 등 내 구현이 든 파일)
  → **절대 덮어쓰지 말고 "병합"** 한다. 내 구현은 유지하고, main 이 **새로 추가한 부분(심화 TODO·스캐폴드)만** 얹는다.

> ⚠️ **가장 중요**: `git checkout origin/main -- <파일>` (덮어쓰기)는 **SAFE 파일에만** 쓴다. **내가 손댄 파일(MINE)에 쓰면 내 구현이 사라진다.** MINE 파일은 반드시 병합(§4)으로 처리.

### 충돌 해소 규칙 (Claude) — 방법 A 패치 실패분·방법 B 병합 공통

패치(방법 A)가 실패했거나 병합(방법 B)에서 충돌이 나면, 각 충돌 파일에서:

1. **내가 구현한 코드(내가 채운 함수 본문 등)** → **내 것 유지**.
2. **main 이 새로 추가한 TODO/함수/스캐폴드(= 이번 심화 등)** → **받아들여 추가**.
3. **main 이 "이전 주차 답안"을 재발행해 내 구현 자리를 덮으려는 것** → **내 것 유지(답안 버림)**.
4. **판단이 애매한 hunk** → 그 부분을 학생에게 보여주고 **어느 쪽을 남길지 물어본다.** 임의로 지우지 않는다.

---

## 4. 반영 방법 (대상 브랜치에서 실행)

> 공통: 진행 중인 머지가 있으면 **먼저 `git merge --abort` 로 정리**한 뒤 시작. `--force` push 금지.

**대상 브랜치 정하기** — "이번 주차 내 작업이 들어있는 브랜치":

```bash
# week 브랜치에 final 보다 앞선 커밋(=내 작업)이 있으면 그 브랜치, 없으면 final
WORK="$NAME/final"
for b in $(git branch -r --format='%(refname:short)' | grep "origin/$NAME/week"); do
  ahead=$(git rev-list --count "origin/$NAME/final..$b" 2>/dev/null || echo 0)
  [ "$ahead" -gt 0 ] && WORK="${b#origin/}"      # 예: gildong/week2
done
echo "대상 브랜치(WORK) = $WORK"
git checkout "$WORK"; git pull --ff-only origin "$WORK" 2>/dev/null || true
```

### 방법 A — 오늘 추가분만 골라 적용 (권장, 무충돌 지향)

이번에 새로 올라온 sync 커밋이 바꾼 파일만, **파일 성격에 맞게** 반영한다. 핵심: **내 구현이 든 파일은 "덮어쓰기"가 아니라 "오늘 추가된 부분만 패치"** 로 얹는다(그래서 week01 답안 재발행·add/add 같은 충돌을 피함).

```bash
# 이번 배포의 강사 sync 커밋 (보통 최신 1개; 오늘 여러 개면 오래된 것부터 순서대로 반복)
SYNC=$(git rev-list -1 --author=kakaotechcampus-bot origin/main)
echo "적용할 sync 커밋:"; git show --stat "$SYNC" | head -20
MB=$(git merge-base HEAD origin/main)

for f in $(git show --name-only --format= "$SYNC"); do
  if git diff --quiet "$MB" HEAD -- "$f"; then
    # SAFE(내가 안 건드린 파일: 프레임워크·신규 파일) → 그대로 받기
    git checkout "$SYNC" -- "$f" && echo "SAFE 반영: $f"
  else
    # MINE(내 구현이 든 파일) → 오늘 추가분만 패치로 얹기 (덮어쓰기 금지)
    # git apply --3way 의 RC 는 상황따라 다르므로, 해시 변화 + 충돌마커로 판정한다.
    before=$(git hash-object "$f")
    git show "$SYNC" -- "$f" | git apply --3way --recount 2>/dev/null || true
    if grep -q '^<<<<<<<' "$f"; then
      echo "⚠️ $f 겹침 충돌 → §3 규칙으로 해소 (내 구현은 살아있음, 조용히 안 사라짐)"
    elif [ "$(git hash-object "$f")" != "$before" ]; then
      echo "MINE 패치 적용(내 구현 유지 + 추가분 반영): $f"
    else
      echo "⚠️ $f 패치 적용 안 됨 → 되돌리고(git checkout -- '$f') 방법 B 로 처리하거나 학생에게 확인"
    fi
  fi
done
git add -A && git commit -m "chore: 새 강의자료(오늘 추가분) 반영"
git push origin "$WORK"
```

> - `git apply` 가 **깨끗이 적용** = 오늘 추가분이 내 코드와 안 겹침(심화가 새 영역에 추가) → 내 구현 그대로 + 심화 반영. ✅
> - **적용 실패** = 오늘 변경이 내가 짠 자리와 겹침 → 방법 B 로 넘기거나 그 hunk 를 학생에게 보여주고 확인.
> - Case 1(이미 PR 있음)이면 같은 `WORK` 브랜치에 push → **기존 PR 자동 갱신** (새 PR 불필요).

### 방법 B — 전체 병합 (fallback)

방법 A가 지저분하거나, **여러 업데이트가 밀린 경우(=어제 것도 안 함)** 한 번에 따라잡을 때.

```bash
git merge --no-edit origin/main     # §3 원칙으로 충돌 해소 (내 구현 유지 + 새 과제 추가, 이전주차 답안은 버림)
# 충돌 해소 후: git add -A && git commit
git push origin "$WORK"
```

> 밀린 업데이트를 한 번에 흡수하지만 week01 답안·week02 add/add 등 **충돌이 여러 개** 날 수 있다. 반드시 §3 규칙으로 해소(내 구현 유지).

### 케이스 → 방법 매핑

| 케이스                        | 상태                                    | 권장                                              |
| ----------------------------- | --------------------------------------- | ------------------------------------------------- |
| **1. 어제 업데이트 + PR까지** | `WORK=<이름>/week2`, 어제분 반영됨      | **방법 A** (같은 브랜치 push → 기존 PR 자동 갱신) |
| **2. 업데이트했지만 PR 전**   | `WORK=<이름>/week2` 또는 final          | **방법 A**                                        |
| **3. 어제 것 아직 안 함**     | final 이 main 과 크게 벌어짐(어제+오늘) | **방법 B** (한 번에 따라잡기)                     |
| **4. 꼬임 / 충돌**            | 진행 중 머지·덮임                       | 아래 복구 후 A/B                                  |

**Case 4 복구:**

```bash
git merge --abort 2>/dev/null; git rebase --abort 2>/dev/null    # 진행 중 작업 정리
# 내 구현이 답안·stub 으로 덮였으면 내 작업 브랜치에서 되살리기 (WK1=첫 주차 브랜치)
WK1=$(git branch -r --format='%(refname:short)' | grep "origin/$NAME/week" | sort | head -1)
git checkout "${WK1#origin/}" -- <덮인 파일>
```

정리·복구 후 방법 A(또는 B)로 진행.

---

## 5. 최종 검증 (반드시 실행 — 여기서 통과해야 끝)

```bash
# (1) 미해결 충돌 마커가 남아있지 않아야 한다 (0이어야 함)
grep -rn '^<<<<<<<\|^=======$\|^>>>>>>>' student_parts fixed run.sh 2>/dev/null \
  && echo "❌ 충돌 마커 남음 → §3 규칙으로 해소 필요" \
  || echo "✅ 충돌 마커 없음"

# (2) 내 구현이 통째로 날아가지 않았는지 — 이번 조치가 바꾼 내용 리뷰
#     student_parts 의 내 파일에서 대량 삭제(-)가 보이면 내 구현이 덮인 신호 → 중단하고 확인
git diff --stat "origin/$WORK" -- student_parts
echo "↑ student_parts 에서 큰 삭제가 있으면 내 구현이 덮였을 수 있음 — 학생과 확인"

# (3) 새 강의자료(오늘 sync 파일)가 전부 반영됐는가
git show --name-only --format= "$SYNC" | while read -r x; do
  [ -z "$x" ] && continue
  test -e "$x" && echo "✅ 존재: $x" || echo "❌ 없음: $x"
done

# (4) (선택) 내 구현이 내 작업 브랜치 것과 맞는지 정밀 비교
#     예: WK1 브랜치의 week01 이 그대로인지
# git diff --quiet "${WK1#origin/}" -- student_parts/week01_wake_up_nana.py && echo "✅ week01 내 구현 유지"

# (5) (선택) 실제 실행 — 이번 주차 실행
# ./run.sh --week2      # 또는 해당 주차 실행 명령
```

- (1) 충돌 마커 없음 + (2) 내 구현 파일에 대량 삭제 없음 + (3) 새 파일 전부 존재 → 완료.
- ⚠️ 하나라도 이상하면 임의로 넘어가지 말고 §4 로 돌아가거나 학생에게 확인한다.

작업 브랜치가 `<이름>/final` 뿐이었다면 이제 이번 주차 브랜치를 판다:

```bash
git checkout -b "$NAME/week2" 2>/dev/null || git checkout "$NAME/week2"   # ★ 'week2' 단독 금지, 반드시 '<이름>/week2'
# 이제 과제(기본/심화) 진행 → 다 하면 PR (base = <이름>/final)
```

---

## 6. Claude 가 반드시 지킬 것 (안전 규칙)

- **학생 코드 유실 방지 최우선.** 내가 손댄 파일(MINE)은 **덮어쓰기 금지, 병합만**. 원본은 항상 `origin/<이름>/week*` 브랜치에 있다 — 확신 없으면 거기서 복구.
- **`git checkout origin/main -- <파일>` 는 SAFE 파일에만.** MINE 파일에 쓰지 않는다.
- **`git push --force` 금지.**
- **이름/브랜치 감지가 불확실하거나, 충돌 hunk 판단이 애매하면 — 실행하지 말고 학생에게 물어본다.**
- 진행 중 머지가 꼬였으면 새 파괴적 명령보다 **`git merge --abort` 로 원위치** 후 재시작.

---

### (운영 메모 — 배포 전 확인용, 학생 안내엔 미포함)

- 이 v2 는 파일 목록을 **런타임에 `git diff` 로 자동 산출**하므로, 이번 심화 업데이트가 **어떤 파일을 건드리든**(신규 파일이면 SAFE, 기존 week02 파일에 추가면 MINE 병합) 자동 대응한다.
- **저녁 강사 base code 업데이트 직후 검증 절차**: 학교 repo 1곳에서
  `git fetch origin` →
  `SYNC=$(git rev-list -1 --author=kakaotechcampus-bot origin/main)` →
  `git show --stat "$SYNC"` 로 심화가 건드린 파일 확인 →
  실제 학생 1명의 `<이름>/week2`(또는 final)에서 §4 병합을 dry-run 해 **① 내 기본 구현 보존 ② 심화 TODO 반영 ③ 충돌 시 §3 규칙으로 해소되는지** 확인.
- 만약 심화가 week02 파일의 **기본 영역(학생이 채운 자리)까지 수정**하면(=append-only 아님) 충돌이 광범위해질 수 있으니, 그때만 §3 에 "기본 영역은 무조건 keep-mine" 예시 hunk 를 1개 덧붙여 재배포.
