---
name: prompt-engineering
description: LLM agent의 system prompt·tool description을 설계하거나 정교화할 때 따르는 프롬프트 엔지니어링 원칙. few-shot/CoT/Instruction 선택 기준, 구조화 출력 유도법, tool description 작성법, 프롬프트 길이·검증 원칙을 정의한다.
user-invocable: false
---

# 프롬프트 엔지니어링 원칙 (Prompt Engineering)

agent의 system prompt나 tool description을 설계·정교화할 때 아래 원칙을 적용한다.

## 1. 기법 4종과 선택 기준

| 기법 | 정의 | 언제 쓰나 |
| --- | --- | --- |
| **zero-shot** | 예시 없이 지시만 | 출력 형태가 자유로워도 될 때. **출력이 제각각이 됨** |
| **few-shot** | 입력→출력 예시를 제공 | **구조화된 출력이 필요할 때 (필수)** |
| **Instruction** | 출력을 명시적으로 제약 | 값 집합을 좁힐 때 ("N개 중 하나로만") |
| **CoT** | 절차를 단계로 분해 | 다단계 판단·계산이 필요할 때 |

## 2. ⭐ 구조화 출력에는 few-shot이 핵심
- **"return 받고 싶은 형태를 그대로 보여주면 모델이 그대로 출력한다."**
- structured output(Pydantic schema 등)을 쓰더라도, 프롬프트에 **입력 → 기대 출력 JSON** 예시를 넣으면 정확도가 올라간다.
- 예시는 **충분한 케이스 1개 + 불충분한 케이스 1개**를 함께 보여주면 "모르면 비워둔다"는 규칙이 잘 전달된다.

## 3. Instruction으로 출력 제약
- 허용값이 유한하면 명시적으로 못 박는다: "`kind`는 A/B/C/D/E **중 하나로만** 채운다. 그 밖의 값은 만들지 않는다."
- 최종 출력 형식도 강제한다: "최종 답변은 **반드시** X 형식으로 낸다."

## 4. CoT로 절차 분해
- 판단이 여러 단계면 번호로 쪼갠다: ①종류 분류 → ②날짜 정규화 → ③시간 정규화 → ④엔티티 추출 → ⑤불확실하면 비움.
- 각 단계에 "판단 근거가 없으면 null" 같은 탈출 조건을 함께 준다.

## 5. tool description 작성법
- **tool 검색은 description을 입력으로 받는다** → description이 곧 tool 선택 신호다.
- 담을 것: (a) 이 tool이 **언제 선택되어야 하는지** 1문장, (b) **인자 형식/제약**(예: `date`는 YYYY-MM-DD).
- 필요하면 docstring에 few-shot 예시(프롬프트 → 기대 인자 JSON)를 덧붙인다.
- tool 실행에는 인자가 필요하고, 그 인자는 프롬프트에서 추출된다(= tool calling). 그래서 system prompt와 description이 길어지는 건 자연스럽다.

## 6. 배치와 길이
- **앞쪽 프롬프트가 중요하다.** 핵심 규칙을 앞에 둔다.
- **markdown 헤더/불릿으로 구조화**하면 모델이 규칙을 더 잘 분리한다.
- **lost in the middle** — 긴 컨텍스트 중간의 내용은 유실되기 쉽다. 무한정 늘리지 말고 **few-shot 예시는 1~2개로 절제**한다.
- 프롬프트 조각을 합칠 때 "뒤 지시가 앞 지시를 우선한다"는 규약이 있으면, **가장 강한 제약을 마지막에** 둔다.

## 7. 검증은 trace로
- agent 구현에는 **컴파일 에러가 없다.** "일단 동작은 된다"가 함정이다.
- 프롬프트 **품질은 정적 검사로 판정할 수 없다.** 반드시 실행해 **trace(tool call·structured output)를 보고 튜닝**한다.
- 확인할 것: 의도한 tool이 선택됐는가, 인자가 정확히 추출됐는가, 출력이 기대 스키마인가.
- **tool call 횟수는 최소화**한다.
- few-shot 예시에 구체적 날짜/값을 넣었다면, 모델이 **예시 값을 실제 출력에 그대로 복사하지 않는지** 반드시 trace로 확인한다(예시 오염).
