import streamlit as st
import json
import os
import random
from pathlib import Path

TESTS_DIR = Path(__file__).parent / "tests"


def load_available_tests():
    """Load all available test files from the tests directory."""
    tests = {}
    if TESTS_DIR.exists():
        for file in TESTS_DIR.glob("*.json"):
            test_name = file.stem.replace("_", " ").title()
            tests[test_name] = file
    return tests


def load_test_questions(file_path):
    """Load questions from a test file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_tags_from_questions(questions):
    """Extract unique tags from questions."""
    return sorted(set(q["tag"] for q in questions))


def select_balanced_questions(questions, selected_tags, num_questions):
    """Select questions balanced across selected tags."""
    filtered = [q for q in questions if q["tag"] in selected_tags]

    if not filtered:
        return []

    if num_questions >= len(filtered):
        random.shuffle(filtered)
        return filtered

    questions_by_tag = {}
    for q in filtered:
        tag = q["tag"]
        if tag not in questions_by_tag:
            questions_by_tag[tag] = []
        questions_by_tag[tag].append(q)

    for tag in questions_by_tag:
        random.shuffle(questions_by_tag[tag])

    selected = []
    tag_list = list(questions_by_tag.keys())
    tag_index = 0

    while len(selected) < num_questions:
        tag = tag_list[tag_index % len(tag_list)]
        if questions_by_tag[tag]:
            selected.append(questions_by_tag[tag].pop(0))
        else:
            tag_list.remove(tag)
            if not tag_list:
                break
        tag_index += 1

    random.shuffle(selected)
    return selected


def reset_quiz():
    """Reset quiz state."""
    for key in ["quiz_started", "questions", "current_index", "answered",
                "score", "show_result", "selected_answer", "wrong_questions",
                "round_history", "current_round"]:
        if key in st.session_state:
            del st.session_state[key]


def main():
    st.set_page_config(page_title="Tests Anatomia", page_icon="📚")
    st.title("Tests Anatomia")

    if "quiz_started" not in st.session_state:
        st.session_state.quiz_started = False

    available_tests = load_available_tests()

    if not available_tests:
        st.error("No hay tests disponibles. Agrega archivos JSON en la carpeta 'tests'.")
        return

    if not st.session_state.quiz_started:
        st.header("Selecciona un test")

        selected_test = st.selectbox(
            "Test disponible:",
            options=list(available_tests.keys())
        )

        if selected_test:
            questions = load_test_questions(available_tests[selected_test])
            tags = get_tags_from_questions(questions)

            st.subheader("Configuracion")

            num_questions = st.number_input(
                "Numero de preguntas:",
                min_value=1,
                max_value=len(questions),
                value=min(25, len(questions))
            )

            st.write("**Temas a incluir:**")
            selected_tags = []
            cols = st.columns(2)
            for i, tag in enumerate(tags):
                tag_display = tag.replace("_", " ").title()
                if cols[i % 2].checkbox(tag_display, value=True, key=f"tag_{tag}"):
                    selected_tags.append(tag)

            if not selected_tags:
                st.warning("Selecciona al menos un tema.")
            else:
                filtered_count = len([q for q in questions if q["tag"] in selected_tags])
                st.info(f"Preguntas disponibles con los temas seleccionados: {filtered_count}")

                if st.button("Comenzar Test", type="primary"):
                    quiz_questions = select_balanced_questions(
                        questions, selected_tags, num_questions
                    )
                    st.session_state.questions = quiz_questions
                    st.session_state.current_index = 0
                    st.session_state.score = 0
                    st.session_state.answered = False
                    st.session_state.show_result = False
                    st.session_state.selected_answer = None
                    st.session_state.wrong_questions = []
                    st.session_state.round_history = []
                    st.session_state.current_round = 1
                    st.session_state.quiz_started = True
                    st.rerun()

    else:
        questions = st.session_state.questions
        current_index = st.session_state.current_index

        if current_index >= len(questions):
            current_round = st.session_state.get("current_round", 1)
            score = st.session_state.score
            total = len(questions)
            wrong = st.session_state.get("wrong_questions", [])

            # Save current round to history if not already saved
            history = st.session_state.get("round_history", [])
            if len(history) < current_round:
                history.append({
                    "round": current_round,
                    "score": score,
                    "total": total,
                    "wrong": list(wrong),
                })
                st.session_state.round_history = history

            st.header("Ronda completada!")

            # Current round result
            percentage = (score / total) * 100
            st.subheader(f"Ronda {current_round}")
            st.metric("Puntuacion", f"{score}/{total} ({percentage:.1f}%)")

            if percentage >= 80:
                st.success("Excelente!")
            elif percentage >= 60:
                st.info("Buen trabajo!")
            else:
                st.warning("Sigue practicando!")

            # Accumulated summary across all rounds
            if len(history) > 1:
                st.divider()
                st.subheader("Resumen acumulado")
                total_all = sum(r["total"] for r in history)
                correct_all = sum(r["score"] for r in history)
                pct_all = (correct_all / total_all) * 100
                st.metric("Total acumulado", f"{correct_all}/{total_all} ({pct_all:.1f}%)")

                for r in history:
                    r_pct = (r["score"] / r["total"]) * 100
                    icon = "✓" if r_pct == 100 else "○"
                    st.write(f"{icon} **Ronda {r['round']}:** {r['score']}/{r['total']} ({r_pct:.1f}%)")

            # Show wrong questions from current round
            if wrong:
                st.divider()
                st.subheader(f"Preguntas falladas en esta ronda ({len(wrong)})")
                for i, q in enumerate(wrong, 1):
                    tag_display = q["tag"].replace("_", " ").title()
                    with st.expander(f"{i}. {q['question']}"):
                        st.caption(f"Tema: {tag_display}")
                        correct = q["options"][q["answer_index"]]
                        st.success(f"Respuesta correcta: {correct}")
                        st.info(f"**Explicacion:** {q['explanation']}")

                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Repetir preguntas falladas", type="primary"):
                        next_round = current_round + 1
                        random.shuffle(wrong)
                        st.session_state.questions = wrong
                        st.session_state.current_index = 0
                        st.session_state.score = 0
                        st.session_state.answered = False
                        st.session_state.selected_answer = None
                        st.session_state.wrong_questions = []
                        st.session_state.current_round = next_round
                        st.rerun()
                with col2:
                    if st.button("Volver al inicio"):
                        reset_quiz()
                        st.rerun()
            else:
                if st.button("Volver al inicio"):
                    reset_quiz()
                    st.rerun()
            return

        question = questions[current_index]

        col1, col2 = st.columns([3, 1])
        with col1:
            st.progress((current_index) / len(questions))
        with col2:
            st.write(f"Pregunta {current_index + 1}/{len(questions)}")

        st.subheader(question["question"])

        tag_display = question["tag"].replace("_", " ").title()
        st.caption(f"Tema: {tag_display}")

        if not st.session_state.answered:
            for i, option in enumerate(question["options"]):
                if st.button(option, key=f"option_{i}", use_container_width=True):
                    st.session_state.selected_answer = i
                    st.session_state.answered = True
                    if i == question["answer_index"]:
                        st.session_state.score += 1
                    else:
                        st.session_state.wrong_questions.append(question)
                    st.rerun()

        else:
            correct_index = question["answer_index"]
            selected = st.session_state.selected_answer

            for i, option in enumerate(question["options"]):
                if i == correct_index:
                    st.success(f"✓ {option}")
                elif i == selected and selected != correct_index:
                    st.error(f"✗ {option}")
                else:
                    st.write(f"  {option}")

            if selected == correct_index:
                st.success("Correcto!")
            else:
                st.error("Incorrecto")

            st.info(f"**Explicacion:** {question['explanation']}")

            if st.button("Siguiente pregunta", type="primary"):
                st.session_state.current_index += 1
                st.session_state.answered = False
                st.session_state.selected_answer = None
                st.rerun()

        st.divider()
        if st.button("Abandonar test"):
            reset_quiz()
            st.rerun()


if __name__ == "__main__":
    main()
