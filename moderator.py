# -*- coding: utf-8 -*-
"""
moderator.py — 합성 모더레이터

모더레이터는 '묻는 쪽 AI'다. 하는 일이 세 가지뿐이다.

  1) 10문항을 순서대로 던진다            → next_main_question()
  2) 답을 읽고 더 캘지 넘어갈지 정한다    → decide()
  3) 끝난 전사록에서 니즈를 뽑는다        → extract_needs()

2번이 핵심이다. 사람 리서처가 머릿속으로 하는 판단을 글로 적어 둔 것이다.
"""

import json
import re

# ──────────────────────────────────────────────────────────────
# 10문항 — 합성사용자와 실제 보호자에게 똑같이 쓴다.
# 질문이 달라지면 두 데이터를 비교할 수 없다.
# ──────────────────────────────────────────────────────────────
QUESTIONS = [
    {"no": "Q01", "block": "사건 복기",
     "text": "최근에 반려동물 데리고 병원 갔던 일 중에서, 기억에 남는 한 번만 떠올려 주세요. 무슨 일이었어요?",
     "goal": "실제로 있었던 한 번의 내원을 특정한다. 일반론을 막는다."},
    {"no": "Q02", "block": "사건 복기",
     "text": "그 병원은 어떻게 고르셨어요?",
     "goal": "선택 경로와 비교 여부를 본다."},
    {"no": "Q03", "block": "사건 복기",
     "text": "접수하고 진료실 들어가기 전까지, 머릿속에 무슨 생각이 있었어요?",
     "goal": "진료 전 기대와 불안의 기준선."},
    {"no": "Q04", "block": "결정 재현",
     "text": "지금 수의사 선생님이 검사 네 가지를 권하면서 견적 40만 원을 보여 줬다고 해 볼게요. 선생님은 기다리고 있어요. 지금 뭐라고 하시겠어요?",
     "goal": "이 질문지의 핵심. 이유를 묻지 않고 그 순간을 다시 세운다."},
    {"no": "Q05", "block": "결정 재현",
     "text": "그때 입 밖으로 안 낸 말이 있었어요?",
     "goal": "못한 말. 가장 중요한 데이터가 여기서 나온다."},
    {"no": "Q06", "block": "결정 재현",
     "text": "결제하고 병원 나왔을 때 기분이 어땠어요? 집에 와서 뭐 하셨어요?",
     "goal": "사후 행동 — 검색, 커뮤니티, 재선택."},
    {"no": "Q07", "block": "관계·마찰",
     "text": "병원에서 묻기 쉬운 질문이랑 묻기 어려운 질문이 따로 있어요?",
     "goal": "침묵의 경계선을 직접 그리게 한다."},
    {"no": "Q08", "block": "관계·마찰",
     "text": "가까운 사람이 똑같은 상황이라면, 뭐라고 해 주시겠어요?",
     "goal": "투사. 본인 얘기로는 말 못 하는 걸 끌어낸다."},
    {"no": "Q09", "block": "동기·니즈",
     "text": "그때 뭐가 있었으면 달랐을 것 같아요?",
     "goal": "기능이 아니라 조건을 말하게 한다."},
    {"no": "Q10", "block": "동기·니즈",
     "text": "'내가 잘 돌보고 있다'는 건 어떤 모습인가요?",
     "goal": "가치. 잠재 니즈가 여기서 나온다."},
]

MAX_PROBES = 3   # 한 문항당 꼬리 질문 최대 횟수. 교수님 지침: 세 번까지 되묻는다.


# ──────────────────────────────────────────────────────────────
# 모더레이터의 직업 규칙
# ──────────────────────────────────────────────────────────────
MODERATOR_SYSTEM = """당신은 숙련된 UX 리서처입니다. 지금 동물병원 보호자 한 명과 1:1 심층 인터뷰를 진행하고 있습니다.
당신의 일은 '답을 듣는 것'이 아니라 '쓸 수 있는 자료를 받아 오는 것'입니다.

[쓸 꼬리 질문의 형태]
- "그때 뭘 물어보고 싶으셨어요?"        → 못한 말을 끌어내는 유일한 통로
- "왜 그렇게 하셨어요?"                 → 같은 답에 최대 세 번까지 되묻는다
- "그 말을 하면 어떤 일이 생길 것 같았어요?"  → 침묵의 비용을 묻는다
- "그게 평소와 달랐나요?"                → 사건을 기준선과 비교하게 한다
- "그때 정확히 어떻게 말하셨어요?"        → 실제 발화를 복원한다

[절대 쓰지 않는 질문]
- "불편하셨죠?" 처럼 답을 미리 정해 주는 유도 질문
- "어떤 기능이 있으면 좋을까요?" 처럼 기능을 묻는 질문 (기존 기능만 나온다)
- "보통 보호자들은…" 처럼 일반론으로 빠지는 질문
- 두 가지를 한 번에 묻는 질문
- 평가, 조언, 위로. 당신은 듣기만 한다.

[더 캐야 하는 답]
- 추상적이다 ("불편했어요", "좀 그랬어요")
- 일반론이다 ("보통 그렇죠", "다들 그러지 않나요")
- 한 문장 단답이다
- 감정만 있고 그 순간 무슨 말을 했는지가 없다
- 금액·항목·장면 같은 구체적 사실이 없다

[넘어가도 되는 답]
- 구체적인 장면이나 실제 발화가 들어 있다
- 금액, 항목, 횟수 같은 사실이 있다
- "그건 제 경험에 없어요"라고 했다 (자료 밖이므로 더 캐면 지어내게 된다)
- 이미 세 번 되물었다

[출력 형식]
반드시 아래 JSON 하나만 출력한다. 설명을 덧붙이지 않는다.
{"action": "probe", "question": "실제로 던질 꼬리 질문 한 문장", "why": "왜 더 캐는지 10자 이내"}
또는
{"action": "next", "why": "왜 충분한지 10자 이내"}

꼬리 질문은 반드시 상대가 방금 한 말에서 단어를 가져와 만든다. 템플릿을 그대로 붙여 쓰지 않는다."""


# ──────────────────────────────────────────────────────────────
# 니즈 추출 — 포스트잇 만드는 단계
# ──────────────────────────────────────────────────────────────
ANALYZER_SYSTEM = """당신은 UX 리서처입니다. 인터뷰 전사록 하나를 읽고 포스트잇에 적을 니즈를 뽑아냅니다.

[세 종류로 나눈다]
- 명시적 : 본인이 말로 꺼낸 것.           예) "총액을 미리 알고 싶다"
- 암묵적 : 당연하게 여겨 말하지 않은 것.    예) "묻는 건 의심하는 걸로 보인다"
- 잠재적 : 본인도 모르는 것. 행동에서만 보인다. 예) "가격 대신 '양심적인 병원'을 검색한다"

[규칙]
1. 한 장에 하나씩. 한 문장으로.
2. 기능으로 적지 말고 니즈로 적는다. "가격표가 있으면 좋겠다"(X) → "결정 전에 총액을 알고 싶다"(O)
3. 반드시 전사록에서 근거가 되는 말을 그대로 따온다. 없는 말을 만들지 않는다.
4. 개인정보보호·신뢰성·편의성 같은 보편적 가치로 묶지 않는다.
5. 암묵적·잠재적이 한 건도 없으면 명시적만 모은 것이다. 다시 읽고 찾는다.
6. 7장에서 10장 사이로 뽑는다.

[출력 형식]
JSON 배열 하나만 출력한다. 설명을 덧붙이지 않는다.
[
  {"type": "명시적", "need": "한 문장 니즈", "quote": "전사록에서 그대로 따온 말", "q": "Q04"},
  ...
]"""


# ──────────────────────────────────────────────────────────────
# 함수들
# ──────────────────────────────────────────────────────────────
def _parse_json(text: str, fallback):
    """모델이 JSON 앞뒤에 말을 붙였을 때도 건져낸다."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
    return fallback


def decide(client, model, question, transcript, last_answer, probe_count):
    """
    모더레이터의 판단. 더 캘지(probe) 넘어갈지(next) 정한다.
    probe_count가 MAX_PROBES에 닿으면 모델을 부르지 않고 바로 넘어간다 — 호출비 절약.
    """
    if probe_count >= MAX_PROBES:
        return {"action": "next", "why": f"되묻기 {MAX_PROBES}회 도달"}

    if "경험에 없" in last_answer or "모르겠어요" in last_answer[-20:]:
        return {"action": "next", "why": "자료 밖 — 더 캐면 지어낸다"}

    recent = "\n".join(f"{r['role']}: {r['text']}" for r in transcript[-6:])
    user = f"""[지금 문항]
{question['no']} {question['text']}
이 문항의 목적: {question['goal']}

[최근 대화]
{recent}

[방금 받은 답]
{last_answer}

[이 문항에서 이미 되물은 횟수]
{probe_count}회 (최대 {MAX_PROBES}회)

판단해 주세요."""

    resp = client.messages.create(
        model=model,
        max_tokens=400,
        system=MODERATOR_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    out = _parse_json(resp.content[0].text, {"action": "next", "why": "판단 실패"})
    if out.get("action") == "probe" and not out.get("question"):
        out = {"action": "next", "why": "꼬리 질문 비어 있음"}
    return out


def ask_persona(client, model, system_prompt, transcript, question_text):
    """보호자 AI에게 질문을 던지고 답을 받는다."""
    messages = []
    for r in transcript:
        messages.append({
            "role": "user" if r["role"] == "모더레이터" else "assistant",
            "content": r["text"],
        })
    messages.append({"role": "user", "content": question_text})

    resp = client.messages.create(
        model=model,
        max_tokens=600,
        system=system_prompt,
        messages=messages,
    )
    return resp.content[0].text.strip()


def extract_needs(client, model, persona_name, transcript):
    """끝난 전사록에서 포스트잇용 니즈를 뽑는다."""
    body = "\n".join(f"{r['role']}: {r['text']}" for r in transcript)
    user = f"[인터뷰 대상] {persona_name} (합성사용자)\n\n[전사록]\n{body}"

    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        system=ANALYZER_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    return _parse_json(resp.content[0].text, [])


def transcript_to_markdown(persona_label_text, mode, transcript):
    """전사록을 파일로 받을 수 있게 마크다운으로 바꾼다."""
    lines = [
        f"# 합성사용자 인터뷰 전사록",
        f"- 대상: {persona_label_text}",
        f"- 모드: {mode}",
        f"- 질문지: 10문항 (합성·실제 공통)",
        "",
    ]
    for r in transcript:
        tag = "**모더레이터**" if r["role"] == "모더레이터" else f"**{persona_label_text.split(' ·')[0]}**"
        mark = r.get("mark", "")
        lines.append(f"{tag} {mark}\n{r['text']}\n")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# FGI — 좌담회 모드
# 같은 질문을 여러 인물에게 동시에 던진다.
# 1:1에서는 안 나오는 데이터가 여기서 나온다:
#   한 사람이 "못 물었다"고 할 때 옆 사람이 "왜 안 물어봐요?"라고 받는 장면.
# ──────────────────────────────────────────────────────────────
OUT_OF_SCOPE_HINTS = ["경험에 없", "잘 모르겠", "기억이 안", "그건 모르", "모르겠어요"]


def is_out_of_scope(text: str) -> bool:
    """가드레일이 작동한 답인지 판정한다. 화면에 빨간 배지를 띄우는 데 쓴다."""
    tail = text[-60:]
    return any(h in tail for h in OUT_OF_SCOPE_HINTS)


def ask_in_group(client, model, system_prompt, transcript, question, others):
    """
    좌담회에서 한 명이 답하게 한다.
    others = [(이름, 역할, 방금 한 말), ...] — 같은 라운드에서 먼저 답한 사람들.
    """
    if others:
        heard = "\n\n".join(f"{n}({r}): {t}" for n, r, t in others)
        user = f"""{question}

[같은 자리에 있는 다른 참여자가 방금 이렇게 답했습니다]
{heard}

다른 사람 말을 들은 상태에서 답하세요.
동의하든 반박하든 좋지만, 반드시 당신의 근거 자료 안에서만 말합니다.
다른 사람 말을 그대로 따라 하지 마세요. 당신 경험이 다르면 다르다고 하세요."""
    else:
        user = question

    messages = []
    for r in transcript:
        messages.append({
            "role": "user" if r["role"] != "보호자" else "assistant",
            "content": r["text"],
        })
    messages.append({"role": "user", "content": user})

    resp = client.messages.create(
        model=model, max_tokens=600, system=system_prompt, messages=messages
    )
    return resp.content[0].text.strip()


REACTION_QUESTION = """방금 다른 분이 한 말 중에 '나는 좀 다른데' 싶은 대목이 있었나요?
어느 대목이고, 당신 경우는 어떻게 달랐는지 말해 주세요.
다른 의견이 없으면 "저도 비슷해요"라고만 하고 끝내도 됩니다. 억지로 만들지 마세요."""
