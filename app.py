# -*- coding: utf-8 -*-
"""
app.py — 동물병원 보호자 합성사용자 · 합성 모더레이터

실행 순서
  1) 왼쪽에서 인물과 모드를 고른다
  2) [인터뷰 자동 진행] 을 누른다 → 모더레이터가 10문항을 알아서 돈다
  3) [니즈 뽑기] 를 누른다 → 포스트잇 후보가 세 종류로 나뉘어 나온다
  4) 전사록과 포스트잇을 파일로 내려받는다

API 키는 이 파일에 절대 쓰지 않는다. Streamlit Cloud의 Secrets 에만 넣는다.
"""

import os
import json
import io
import csv

import streamlit as st
import anthropic

import knowledge_base as kb
import moderator as mod

st.set_page_config(page_title="동물병원 합성사용자", page_icon="🐾", layout="wide")

MODELS = {
    "Sonnet 5.5 (권장 · 빠르고 저렴)": "claude-sonnet-5-5",
    "Opus 5 (가장 정교함 · 비쌈)": "claude-opus-5",
    "Haiku 4.5 (가장 저렴 · 테스트용)": "claude-haiku-4-5-20251001",
}


# ──────────────────────────────────────────────────────────────
# API 키
# ──────────────────────────────────────────────────────────────
def get_client():
    key = None
    try:
        key = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        st.error(
            "API 키가 없습니다.\n\n"
            "Streamlit Cloud 화면 오른쪽 아래 **Manage app → Settings → Secrets** 에 "
            "아래 한 줄을 넣고 저장하세요.\n\n"
            '```\nANTHROPIC_API_KEY = "sk-ant-..."\n```\n\n'
            "GitHub 파일 안에는 절대 쓰지 마세요. 저장소가 공개라 키가 그대로 노출됩니다."
        )
        st.stop()
    return anthropic.Anthropic(api_key=key)


# ──────────────────────────────────────────────────────────────
# 상태
# ──────────────────────────────────────────────────────────────
def init_state():
    st.session_state.setdefault("transcript", [])
    st.session_state.setdefault("needs", None)
    st.session_state.setdefault("done", False)
    st.session_state.setdefault("log", [])


def reset_interview():
    st.session_state.transcript = []
    st.session_state.needs = None
    st.session_state.done = False
    st.session_state.log = []


init_state()

# ──────────────────────────────────────────────────────────────
# 왼쪽 설정
# ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 인터뷰 설정")

    pid = st.selectbox(
        "인물",
        list(kb.ALL_SPEAKERS.keys()),
        format_func=lambda k: kb.persona_label(k),
    )
    mode = st.radio(
        "모드",
        ["검증", "탐색"],
        help="검증 = 자료에 있는 것만 말한다. 탐색 = 자료 기반 추론을 허용하고 (추론)이라고 표시한다.",
    )
    model_label = st.selectbox("모델", list(MODELS.keys()))
    model = MODELS[model_label]

    st.divider()
    p = kb.ALL_SPEAKERS[pid]
    st.markdown(f"**{p['name']}** · {p['type']}")
    st.caption(p["one_line"])
    if p.get("share"):
        st.caption(f"표본 비중 {p['share']}% · 근거 행 {len(p['evidence'])}건")
    else:
        st.caption(f"공급자 측 · 근거 행 {len(p['evidence'])}건")

    st.divider()
    if st.button("인터뷰 초기화", use_container_width=True):
        reset_interview()
        st.rerun()

    st.caption(
        "인터뷰 1건에 모델 호출이 30~50회 발생합니다. "
        "처음에는 Haiku로 돌려 보고, 자료로 쓸 때만 Sonnet으로 올리세요."
    )


st.title("동물병원 보호자 합성사용자")
st.caption(
    "모더레이터 AI가 10문항을 던지고, 답이 얄팍하면 스스로 꼬리 질문을 만듭니다. "
    "같은 10문항을 실제 보호자에게도 씁니다 — 질문이 달라지면 두 데이터를 비교할 수 없습니다."
)

tab_interview, tab_fgi, tab_needs, tab_profile, tab_evidence = st.tabs(
    ["1:1 인터뷰", "FGI 좌담회", "니즈 · 포스트잇", "인물 프로필", "근거 자료"]
)


# 가드레일 배지 3종. scope_status가 돌려주는 값과 짝이 맞는다.
BADGE = {
    "refusal": ("#DD002A", "⚠ 데이터 범위 외 — 답변 불가"),
    "partial": ("#B26A00", "◐ 일부 답변 — 나머지는 자료 범위 밖"),
}


def badge_html(status, margin_top="8px"):
    """배지 한 개를 그린다. status가 full이면 아무것도 그리지 않는다."""
    if status not in BADGE:
        return ""
    color, label = BADGE[status]
    return (
        f'<div style="display:inline-block;margin-top:{margin_top};'
        f'border:1px solid {color};color:{color};border-radius:4px;'
        f'padding:4px 10px;font-size:13px;font-weight:600">{label}</div>'
    )


def answer_block(name, role, text):
    """답변 한 칸. 자료 범위를 벗어난 정도에 따라 배지를 띄운다."""
    st.markdown(f"**{name}** · {role}")
    status = mod.scope_status(text)
    # 전면 거절일 때만 본문을 흐리게 한다. 일부 답변은 쓸 수 있는 데이터다.
    dim = "color:#6C6F77;font-style:italic" if status == "refusal" else ""
    st.markdown(
        f"""<div style="border:1px solid #DCDCDD;border-radius:6px;padding:14px;{dim}">
        {text}</div>{badge_html(status)}""",
        unsafe_allow_html=True,
    )
    st.write("")


# ──────────────────────────────────────────────────────────────
# 1. 인터뷰
# ──────────────────────────────────────────────────────────────
with tab_interview:
    c1, c2 = st.columns([1, 1])
    run_all = c1.button("인터뷰 자동 진행 (10문항)", type="primary", use_container_width=True)
    run_one = c2.button("한 문항만 진행", use_container_width=True)

    box = st.container()

    def render():
        for r in st.session_state.transcript:
            if r["role"] == "모더레이터":
                with box.chat_message("user", avatar="🎤"):
                    st.markdown(f"**{r.get('mark','')}** {r['text']}")
            else:
                with box.chat_message("assistant", avatar="🐾"):
                    st.markdown(r["text"])
                    html = badge_html(mod.scope_status(r["text"]), margin_top="0")
                    if html:
                        st.markdown(html, unsafe_allow_html=True)

    def run_question(client, system_prompt, q, status):
        """한 문항을 끝까지 진행한다 — 본질문 + 꼬리 질문."""
        st.session_state.transcript.append(
            {"role": "모더레이터", "text": q["text"], "mark": f"[{q['no']}]"}
        )
        answer = mod.ask_persona(
            client, model, system_prompt, st.session_state.transcript[:-1], q["text"]
        )
        st.session_state.transcript.append({"role": "보호자", "text": answer})

        probes = 0
        while probes < mod.MAX_PROBES:
            d = mod.decide(client, model, q, st.session_state.transcript, answer, probes)
            if d.get("action") != "probe":
                st.session_state.log.append(f"{q['no']} 넘어감 — {d.get('why','')}")
                break
            probes += 1
            pq = d["question"]
            st.session_state.log.append(f"{q['no']} 꼬리 {probes} — {d.get('why','')}")
            st.session_state.transcript.append(
                {"role": "모더레이터", "text": pq, "mark": f"[{q['no']}-꼬리{probes}]"}
            )
            answer = mod.ask_persona(
                client, model, system_prompt, st.session_state.transcript[:-1], pq
            )
            st.session_state.transcript.append({"role": "보호자", "text": answer})
            status.write(f"{q['no']} 꼬리 질문 {probes}회")

    if run_all or run_one:
        client = get_client()
        system_prompt = kb.build_persona_system_prompt(pid, mode)

        asked = {r["mark"][1:4] for r in st.session_state.transcript
                 if r["role"] == "모더레이터" and r.get("mark", "").startswith("[Q")}
        todo = [q for q in mod.QUESTIONS if q["no"] not in asked]
        if run_one:
            todo = todo[:1]

        if not todo:
            st.info("10문항을 모두 마쳤습니다. '니즈 · 포스트잇' 탭으로 넘어가세요.")
        else:
            with st.status("인터뷰 진행 중", expanded=True) as status:
                for q in todo:
                    status.update(label=f"{q['no']} · {q['block']}")
                    status.write(q["text"])
                    run_question(client, system_prompt, q, status)
                status.update(label="인터뷰 완료", state="complete")
            if len([q for q in mod.QUESTIONS if q["no"] not in asked]) == len(todo):
                st.session_state.done = True
            st.rerun()

    render()

    if st.session_state.transcript:
        st.divider()
        md = mod.transcript_to_markdown(kb.persona_label(pid), mode, st.session_state.transcript)
        st.download_button(
            "전사록 내려받기 (.md)",
            md,
            file_name=f"전사록_{kb.ALL_SPEAKERS[pid]['name']}_{mode}.md",
            mime="text/markdown",
        )
        with st.expander("모더레이터가 내린 판단 기록"):
            for line in st.session_state.log:
                st.caption(line)

        st.markdown("###### 직접 더 묻기")
        extra = st.chat_input("꼬리 질문을 직접 던져 보세요")
        if extra:
            client = get_client()
            system_prompt = kb.build_persona_system_prompt(pid, mode)
            st.session_state.transcript.append(
                {"role": "모더레이터", "text": extra, "mark": "[직접]"}
            )
            a = mod.ask_persona(
                client, model, system_prompt, st.session_state.transcript[:-1], extra
            )
            st.session_state.transcript.append({"role": "보호자", "text": a})
            st.rerun()


# ──────────────────────────────────────────────────────────────
# 1-2. FGI 좌담회 — 같은 질문을 여러 명에게 동시에
# ──────────────────────────────────────────────────────────────
st.session_state.setdefault("fgi", [])
st.session_state.setdefault("fgi_count", 0)

with tab_fgi:
    st.caption(
        "1:1에서는 안 나오는 데이터가 여기서 나옵니다 — 한 사람이 '못 물었다'고 할 때 "
        "옆 사람이 '저는 물어봤는데요'라고 받는 장면이요."
    )

    picked = st.multiselect(
        "참여자",
        list(kb.ALL_SPEAKERS.keys()),
        default=["P1", "P3", "P4"],
        format_func=lambda k: kb.persona_label(k),
    )
    if False:
        st.info(
            "지금은 보호자만 있습니다. 보호자끼리만 모으면 '병원이 설명을 안 해줬다'로 합창이 끝납니다. "
            "준영 님 패널의 수의사·간호사·가족 근거를 `knowledge_base.py`의 STAKEHOLDERS에 채우면 "
            "'안 물어보셔서 설명을 안 했다'는 반대편 말이 같은 화면에 올라옵니다."
        )

    preset = st.selectbox(
        "질문 고르기",
        ["(직접 입력)"] + [f"{q['no']} {q['text']}" for q in mod.QUESTIONS],
    )
    default_q = "" if preset.startswith("(") else preset[4:]
    gq = st.text_area("질문", value=default_q, height=80)

    cc1, cc2, cc3 = st.columns([1, 1, 1])
    go = cc1.button("좌담회에 질문 던지기", type="primary", use_container_width=True,
                    disabled=not (gq.strip() and picked))
    react = cc2.button("참여자 반응 유도", use_container_width=True,
                       disabled=not st.session_state.fgi)
    if cc3.button("좌담회 초기화", use_container_width=True):
        st.session_state.fgi = []
        st.session_state.fgi_count = 0
        st.rerun()

    def fgi_round(question, label):
        client = get_client()
        others = []
        row = {"q": question, "label": label, "answers": []}
        with st.status(f"{label} 진행 중", expanded=True) as status:
            for k in picked:
                sp = kb.build_persona_system_prompt(k, mode)
                person = kb.ALL_SPEAKERS[k]
                status.update(label=f"{person['name']} 답변 중")
                hist = [
                    {"role": "보호자" if a["key"] == k else "모더레이터", "text": a["text"]}
                    for r in st.session_state.fgi for a in r["answers"] if a["key"] == k
                ]
                ans = mod.ask_in_group(client, model, sp, hist, question, others)
                row["answers"].append(
                    {"key": k, "name": person["name"], "role": person["type"], "text": ans}
                )
                others.append((person["name"], person["type"], ans))
            status.update(label=f"{label} 완료", state="complete")
        st.session_state.fgi.append(row)
        st.session_state.fgi_count += 1

    if go:
        fgi_round(gq.strip(), f"질문 {st.session_state.fgi_count + 1}")
        st.rerun()
    if react:
        fgi_round(mod.REACTION_QUESTION, "반응 유도")
        st.rerun()

    if st.session_state.fgi:
        m1, m2, m3 = st.columns(3)
        m1.metric("질문", st.session_state.fgi_count)
        m2.metric("참여자", len(picked))
        m3.metric("발화", sum(len(r["answers"]) for r in st.session_state.fgi))
        st.divider()

    who = st.radio(
        "보기", ["전체"] + [kb.ALL_SPEAKERS[k]["name"] for k in picked],
        horizontal=True,
    ) if st.session_state.fgi else "전체"

    for r in st.session_state.fgi:
        st.markdown(
            f"""<div style="text-align:right"><span style="background:#CC1C4A;color:#fff;
            padding:10px 16px;border-radius:16px;display:inline-block;max-width:70%">
            {r['label']} · {r['q']}</span></div>""",
            unsafe_allow_html=True,
        )
        st.write("")
        for a in r["answers"]:
            if who != "전체" and a["name"] != who:
                continue
            answer_block(a["name"], a["role"], a["text"])
        st.divider()

    if st.session_state.fgi:
        lines = ["# FGI 좌담회 전사록", f"- 모드: {mode}", ""]
        for r in st.session_state.fgi:
            lines.append(f"## {r['label']} — {r['q']}\n")
            for a in r["answers"]:
                _mark = {"refusal": " `[데이터 범위 외]`", "partial": " `[일부 답변]`"}
                flag = _mark.get(mod.scope_status(a["text"]), "")
                lines.append(f"**{a['name']} · {a['role']}**{flag}\n\n{a['text']}\n")
        st.download_button(
            "좌담회 전사록 내려받기 (.md)", "\n".join(lines),
            file_name=f"FGI전사록_{mode}.md", mime="text/markdown",
        )


# ──────────────────────────────────────────────────────────────
# 2. 니즈 · 포스트잇
# ──────────────────────────────────────────────────────────────
with tab_needs:
    sources = []
    if st.session_state.transcript:
        sources.append("1:1 인터뷰")
    if st.session_state.fgi:
        sources.append("FGI 좌담회")

    if not sources:
        st.info("먼저 인터뷰나 좌담회를 진행하세요.")
    else:
        src = st.radio("어느 전사록에서 뽑을까요", sources, horizontal=True)
        if st.button("니즈 뽑기", type="primary"):
            client = get_client()
            if src == "1:1 인터뷰":
                who_label = kb.ALL_SPEAKERS[pid]["name"]
                tr = st.session_state.transcript
            else:
                who_label = "FGI 참여자 전원"
                tr = []
                for r in st.session_state.fgi:
                    tr.append({"role": "모더레이터", "text": r["q"]})
                    for a in r["answers"]:
                        tr.append({"role": a["name"], "text": a["text"]})
            with st.spinner("전사록에서 니즈를 뽑는 중"):
                st.session_state.needs = mod.extract_needs(client, model, who_label, tr)

        needs = st.session_state.needs
        if needs:
            cols = st.columns(3)
            for col, kind in zip(cols, ["명시적", "암묵적", "잠재적"]):
                items = [n for n in needs if n.get("type") == kind]
                col.markdown(f"#### {kind} · {len(items)}장")
                if not items:
                    col.warning("한 장도 없습니다. 전사록을 다시 읽어야 합니다.")
                for n in items:
                    col.markdown(
                        f"""<div style="background:#FFF8FA;border:1px solid #DCDCDD;
                        padding:14px;margin-bottom:10px;border-radius:4px">
                        <div style="font-size:12px;color:#CC1C4A;font-weight:700">
                        S · {n.get('q','')}</div>
                        <div style="font-size:15px;font-weight:600;margin:6px 0">
                        {n.get('need','')}</div>
                        <div style="font-size:13px;color:#6C6F77">"{n.get('quote','')}"</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )

            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["출처", "인물", "니즈종류", "니즈", "근거 인용", "문항"])
            for n in needs:
                w.writerow(["S(합성)", kb.ALL_SPEAKERS[pid]["name"], n.get("type", ""),
                            n.get("need", ""), n.get("quote", ""), n.get("q", "")])
            st.download_button(
                "포스트잇 내려받기 (.csv)",
                buf.getvalue().encode("utf-8-sig"),
                file_name=f"포스트잇_{kb.ALL_SPEAKERS[pid]['name']}.csv",
                mime="text/csv",
            )
            st.caption(
                "S는 합성사용자, H는 실제 보호자입니다. 두 데이터를 섞지 마세요 — "
                "섞는 순간 비교라는 이번 과제의 목적이 사라집니다."
            )


# ──────────────────────────────────────────────────────────────
# 3. 인물 프로필
# ──────────────────────────────────────────────────────────────
with tab_profile:
    p = kb.ALL_SPEAKERS[pid]
    st.markdown(f"## {p['name']}")
    st.markdown(f"**{p['type']}**" + (f" · 표본 비중 {p['share']}%" if p.get("share") else ""))
    st.markdown(f"> {p['one_line']}")
    c = st.columns(5)
    for col, (title, key) in zip(
        c,
        [("인구통계", "demographics"), ("행동·경험", "behavior"),
         ("심리특성", "psychology"), ("맥락", "context"), ("말투", "voice")],
    ):
        col.markdown(f"##### {title}")
        for x in p[key]:
            col.markdown(f"- {x}")
    st.divider()
    st.caption(
        "(추정)이 붙은 값은 데이터에 없어서 통계로 채운 칸입니다. "
        "132건 중 보호자 나이를 밝힌 글이 2건뿐이라, 네 인물의 나이는 전부 추정값입니다. "
        "검증 모드에서는 이 값을 묻지 않습니다."
    )


# ──────────────────────────────────────────────────────────────
# 4. 근거 자료
# ──────────────────────────────────────────────────────────────
with tab_evidence:
    st.markdown(f"#### {kb.ALL_SPEAKERS[pid]['name']}에게 주입된 근거 {len(kb.ALL_SPEAKERS[pid]['evidence'])}건")
    for n in kb.ALL_SPEAKERS[pid]["evidence"]:
        if n in kb.EVIDENCE:
            st.markdown(f"**#{n}** {kb.EVIDENCE[n]}")
    st.divider()
    st.markdown("#### 인용 금지 행")
    for n, why in kb.BANNED.items():
        st.markdown(f"**#{n}** — {why}")
