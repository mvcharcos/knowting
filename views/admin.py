import streamlit as st
from translations import t
from auth import _is_global_admin, _is_pending_approval
from db import (
    get_all_users_with_roles, set_user_global_role, delete_user_account,
    get_user_survey_status, create_user_survey_status, update_user_survey_status,
    revoke_survey_based_access, approve_knower_access, approve_knowter_access,
    get_users_pending_approval, get_users_needing_survey, get_users_with_overdue_surveys,
    get_pending_approval_count,
    create_survey, update_survey, delete_survey, get_survey, get_all_surveys,
    set_active_survey, get_active_periodic_survey, get_active_initial_survey,
    add_survey_question, update_survey_question, delete_survey_question,
    get_survey_questions, get_next_survey_question_num,
    submit_survey_response, has_completed_survey,
    get_survey_responses, get_survey_response_answers, get_survey_answer_statistics,
    put_access_on_hold, release_access_hold,
)


def show_admin_panel():
    """Show the admin panel for managing user roles."""
    if not _is_global_admin():
        st.error(t("no_permission"))
        return

    st.header(t("admin_panel"))
    st.write(t("admin_panel_desc"))

    # Get all users
    users = get_all_users_with_roles()

    if not users:
        st.info(t("no_users"))
        return

    # Search filter
    search = st.text_input(t("search_user"), placeholder=t("search_user_placeholder"), key="admin_user_search")

    filtered_users = users
    if search:
        search_lower = search.lower()
        filtered_users = [u for u in users if search_lower in u["email"].lower() or (u["display_name"] and search_lower in u["display_name"].lower())]

    st.write(t("total_users", n=len(filtered_users)))

    # Role options (student role removed from global roles - only applies at test/program level)
    role_options = ["tester", "visitor", "knower", "knowter", "admin"]
    role_labels = {
        "tester": t("global_role_tester"),
        "visitor": t("global_role_visitor"),
        "knower": t("global_role_knower"),
        "knowter": t("global_role_knowter"),
        "admin": t("global_role_admin"),
    }

    # Display users in a table-like format
    for user in filtered_users:
        display = user["display_name"] or user["email"]
        current_role = user["global_role"] or "knower"
        # Handle legacy roles by treating them as 'knower'
        if current_role in ("student", "free"):
            current_role = "knower"
        elif current_role == "premium":
            current_role = "knowter"
        current_idx = role_options.index(current_role) if current_role in role_options else 0
        is_self = user["id"] == st.session_state.user_id

        col_info, col_role, col_delete = st.columns([3, 2, 0.5])
        with col_info:
            st.write(f"**{display}**")
            if user["display_name"]:
                st.caption(user["email"])
        with col_role:
            new_role = st.selectbox(
                t("role"),
                options=role_options,
                index=current_idx,
                format_func=lambda x: role_labels.get(x, x),
                label_visibility="collapsed",
                key=f"role_select_{user['id']}",
            )
            if new_role != current_role:
                set_user_global_role(user["id"], new_role)
                st.rerun()
        with col_delete:
            if is_self:
                st.button("\U0001f5d1\ufe0f", key=f"delete_user_{user['id']}", disabled=True, help=t("cannot_delete_self"))
            else:
                with st.popover("\U0001f5d1\ufe0f", help=t("delete_user")):
                    st.warning(t("delete_user_confirm", user=display))
                    st.caption(t("delete_user_admin_warning"))
                    if st.button(t("delete_user"), key=f"confirm_delete_{user['id']}", type="primary"):
                        delete_user_account(user["id"])
                        st.success(t("user_deleted", user=display))
                        st.rerun()


# --- Survey Pages ---

def show_survey_page(survey):
    """Display a survey for the user to complete."""
    user_id = st.session_state.get("user_id")

    # Check if user has already completed this survey
    if has_completed_survey(user_id, survey["id"]):
        st.header(f"\U0001f4cb {survey['title']}")
        st.success(t("survey_already_completed"))

        # Show appropriate message based on survey type
        status = get_user_survey_status(user_id)
        if survey["survey_type"] == "initial" and status and status.get("pending_approval"):
            st.info(t("pending_approval_message"))

        if st.button(t("back")):
            st.session_state.page = "Home"
            st.rerun()
        return

    st.header(f"\U0001f4cb {survey['title']}")
    if survey.get("description"):
        st.write(survey["description"])

    st.divider()

    questions = get_survey_questions(survey["id"])
    if not questions:
        st.warning(t("no_questions"))
        return

    # Initialize answers in session state
    if "survey_answers" not in st.session_state:
        st.session_state.survey_answers = {}

    all_required_answered = True

    for q in questions:
        st.write(f"**{q['question_num']}. {q['question_text']}**")
        if q["required"]:
            st.caption(f"* {t('required_field')}")

        key = f"survey_q_{q['id']}"

        if q["question_type"] == "multiple_choice":
            options = q["options"] if q["options"] else []
            if options:
                answer = st.radio(
                    "", options=options,
                    key=key, label_visibility="collapsed"
                )
                st.session_state.survey_answers[q["id"]] = {"answer_text": answer}
            else:
                st.warning("No options defined")
                if q["required"]:
                    all_required_answered = False

        elif q["question_type"] == "text":
            answer = st.text_area("", key=key, label_visibility="collapsed")
            st.session_state.survey_answers[q["id"]] = {"answer_text": answer}
            if q["required"] and not answer.strip():
                all_required_answered = False

        elif q["question_type"] == "rating":
            options = q["options"] if q["options"] else ["1", "2", "3", "4", "5"]
            answer = st.select_slider("", options=options, key=key, label_visibility="collapsed")
            st.session_state.survey_answers[q["id"]] = {"answer_text": answer}

        elif q["question_type"] == "checkbox":
            selected = []
            for i, opt in enumerate(q["options"]):
                if st.checkbox(opt, key=f"{key}_{i}"):
                    selected.append(opt)
            st.session_state.survey_answers[q["id"]] = {"answer_options": selected}
            if q["required"] and not selected:
                all_required_answered = False

        st.divider()

    if st.button(t("submit_survey"), type="primary", disabled=not all_required_answered):
        answers = [
            {"question_id": q_id, **data}
            for q_id, data in st.session_state.survey_answers.items()
        ]
        user_id = st.session_state.get("user_id")
        submit_survey_response(survey["id"], user_id, answers)

        # Update user status based on survey type
        from datetime import datetime, timedelta
        new_deadline = (datetime.now() + timedelta(days=30)).isoformat()
        status = get_user_survey_status(user_id)

        if survey["survey_type"] == "initial":
            # Initial survey completed - set deadline and release any hold
            if status:
                update_user_survey_status(
                    user_id,
                    initial_completed=True,
                    deadline=new_deadline,
                    access_on_hold=False
                )
            else:
                # This shouldn't happen in normal flow, but handle it
                create_user_survey_status(user_id, "survey", initial_completed=True, pending_approval=False)
                update_user_survey_status(user_id, deadline=new_deadline)
        elif survey["survey_type"] == "periodic" and status:
            # Periodic survey - reset deadline and release any hold
            update_user_survey_status(
                user_id,
                last_survey_id=survey["id"],
                deadline=new_deadline,
                access_on_hold=False
            )

        # Clear answers and show success
        if "survey_answers" in st.session_state:
            del st.session_state.survey_answers
        st.success(t("survey_completed"))
        # Return to the page they were trying to access, or home
        return_page = st.session_state.get("return_after_survey", "Home")
        st.session_state.page = return_page
        if "return_after_survey" in st.session_state:
            del st.session_state["return_after_survey"]
        st.rerun()


def show_admin_surveys():
    """Show admin panel for managing surveys."""
    if not _is_global_admin():
        st.error(t("no_permission"))
        return

    st.header(f"\U0001f4cb {t('surveys')}")

    # Navigation tabs
    tab1, tab2, tab3 = st.tabs([t("surveys"), t("users_pending_approval"), t("users_needing_survey")])

    with tab1:
        _show_survey_management()

    with tab2:
        _show_pending_approvals()

    with tab3:
        _show_survey_users()


def _show_survey_management():
    """Show survey list and management UI."""
    # Create new survey button
    if st.button(t("create_survey"), type="primary"):
        st.session_state.creating_survey = True
        st.rerun()

    # Show survey creation form
    if st.session_state.get("creating_survey"):
        _show_survey_creation_form()
        return

    # Show survey editor if editing
    if st.session_state.get("editing_survey_id"):
        _show_survey_editor(st.session_state.editing_survey_id)
        return

    # Show survey statistics if viewing
    if st.session_state.get("viewing_survey_stats"):
        _show_survey_statistics(st.session_state.viewing_survey_stats)
        return

    # List all surveys
    surveys = get_all_surveys()
    if not surveys:
        st.info(t("no_surveys"))
        return

    for survey in surveys:
        with st.container(border=True):
            col_info, col_actions = st.columns([3, 2])
            with col_info:
                title_display = survey["title"]
                if survey["is_active"]:
                    title_display = f"\u2705 {title_display}"
                st.subheader(title_display)
                type_label = t(f"survey_type_{survey['survey_type']}")
                st.caption(f"{t('survey_type')}: {type_label}")
                st.caption(f"{t('n_questions', n=survey['question_count'])} \u00b7 {t('n_responses', n=survey['response_count'])}")

            with col_actions:
                btn_cols = st.columns(4)
                with btn_cols[0]:
                    if st.button("\u270f\ufe0f", key=f"edit_survey_{survey['id']}", help=t("edit")):
                        st.session_state.editing_survey_id = survey["id"]
                        st.rerun()
                with btn_cols[1]:
                    if st.button("\U0001f4ca", key=f"stats_survey_{survey['id']}", help=t("survey_statistics")):
                        st.session_state.viewing_survey_stats = survey["id"]
                        st.rerun()
                with btn_cols[2]:
                    if not survey["is_active"]:
                        if st.button("\u2705", key=f"activate_survey_{survey['id']}", help=t("set_as_active")):
                            set_active_survey(survey["id"], survey["survey_type"])
                            st.success(t("survey_activated"))
                            st.rerun()
                    else:
                        st.button("\u2705", key=f"active_survey_{survey['id']}", disabled=True, help=t("active_survey"))
                with btn_cols[3]:
                    with st.popover("\U0001f5d1\ufe0f", help=t("delete")):
                        st.warning(t("confirm_delete"))
                        if st.button(t("delete"), key=f"confirm_del_survey_{survey['id']}", type="primary"):
                            delete_survey(survey["id"])
                            st.rerun()


def _show_survey_creation_form():
    """Show form to create a new survey."""
    st.subheader(t("create_survey"))

    if st.button(t("back")):
        st.session_state.creating_survey = False
        st.rerun()

    title = st.text_input(t("survey_title"), key="new_survey_title")
    description = st.text_area(t("survey_description"), key="new_survey_desc")

    survey_types = ["initial", "periodic", "feedback"]
    type_labels = {
        "initial": t("survey_type_initial"),
        "periodic": t("survey_type_periodic"),
        "feedback": t("survey_type_feedback"),
    }
    survey_type = st.selectbox(
        t("survey_type"),
        options=survey_types,
        format_func=lambda x: type_labels[x],
        key="new_survey_type"
    )

    if st.button(t("create_survey"), type="primary", key="create_survey_btn"):
        if not title.strip():
            st.warning(t("title_required"))
        else:
            survey_id = create_survey(title.strip(), description.strip(), survey_type)
            st.session_state.creating_survey = False
            st.session_state.editing_survey_id = survey_id
            st.success(t("survey_saved"))
            st.rerun()


def _show_survey_editor(survey_id):
    """Show survey editor with question management."""
    survey = get_survey(survey_id)
    if not survey:
        st.error("Survey not found")
        st.session_state.editing_survey_id = None
        st.rerun()
        return

    st.subheader(f"{t('edit_survey')}: {survey['title']}")

    if st.button(t("back_to_surveys")):
        st.session_state.editing_survey_id = None
        st.rerun()

    # Survey metadata
    with st.expander(t("survey_description"), expanded=False):
        new_title = st.text_input(t("survey_title"), value=survey["title"], key="edit_survey_title")
        new_desc = st.text_area(t("survey_description"), value=survey["description"] or "", key="edit_survey_desc")
        if st.button(t("save"), key="save_survey_meta"):
            update_survey(survey_id, new_title, new_desc, survey.get("valid_from"), survey.get("valid_until"))
            st.success(t("survey_saved"))
            st.rerun()

    st.divider()

    # Questions section
    st.subheader(t("survey_questions"))

    questions = get_survey_questions(survey_id)

    # Add new question form
    with st.expander(f"\u2795 {t('add_survey_question')}", expanded=False):
        q_types = ["multiple_choice", "text", "rating", "checkbox"]
        q_type_labels = {
            "multiple_choice": t("question_type_multiple_choice"),
            "text": t("question_type_text"),
            "rating": t("question_type_rating"),
            "checkbox": t("question_type_checkbox"),
        }

        new_q_text = st.text_area(t("question"), key="new_sq_text")
        new_q_type = st.selectbox(
            t("question_type"),
            options=q_types,
            format_func=lambda x: q_type_labels[x],
            key="new_sq_type"
        )

        # Options for choice-based questions
        new_q_options = []
        if new_q_type in ("multiple_choice", "checkbox", "rating"):
            st.write(t("options"))
            for i in range(5):
                opt = st.text_input(t("option_placeholder", n=i+1), key=f"new_sq_opt_{i}")
                if opt.strip():
                    new_q_options.append(opt.strip())

        new_q_required = st.checkbox(t("required_field"), value=True, key="new_sq_required")

        if st.button(t("add_survey_question"), type="primary", key="add_sq_btn"):
            if not new_q_text.strip():
                st.warning(t("question") + " is required")
            else:
                next_num = get_next_survey_question_num(survey_id)
                add_survey_question(survey_id, next_num, new_q_type, new_q_text.strip(), new_q_options, new_q_required)
                st.success(t("question_saved"))
                st.rerun()

    # Display existing questions
    for q in questions:
        with st.container(border=True):
            col_q, col_actions = st.columns([4, 1])
            with col_q:
                q_type_label = t(f"question_type_{q['question_type']}")
                st.write(f"**{q['question_num']}. {q['question_text']}**")
                st.caption(f"{q_type_label} | {'*' if q['required'] else ''}{t('required_field') if q['required'] else t('optional_field')}")
                if q["options"]:
                    st.caption(f"{t('options')}: {', '.join(q['options'])}")
            with col_actions:
                with st.popover("\U0001f5d1\ufe0f", help=t("delete")):
                    if st.button(t("delete"), key=f"del_sq_{q['id']}", type="primary"):
                        delete_survey_question(q["id"])
                        st.success(t("question_deleted"))
                        st.rerun()


def _show_survey_statistics(survey_id):
    """Show survey response statistics."""
    survey = get_survey(survey_id)
    if not survey:
        st.session_state.viewing_survey_stats = None
        st.rerun()
        return

    st.subheader(f"\U0001f4ca {survey['title']}")

    if st.button(t("back_to_surveys")):
        st.session_state.viewing_survey_stats = None
        st.rerun()

    responses = get_survey_responses(survey_id)
    st.write(t("n_responses", n=len(responses)))

    if not responses:
        st.info(t("no_responses_yet"))
        return

    # Show statistics per question
    stats = get_survey_answer_statistics(survey_id)

    for q_id, stat in stats.items():
        q = stat.get("question", {})
        st.subheader(f"{q.get('question_num', '?')}. {q.get('question_text', 'Unknown')}")

        if "counts" in stat:
            # Multiple choice, rating, checkbox
            counts = stat["counts"]
            if counts:
                import pandas as pd
                df = pd.DataFrame(list(counts.items()), columns=["Option", "Count"])
                st.bar_chart(df.set_index("Option"))
        elif "text_responses" in stat:
            # Text responses
            for i, resp in enumerate(stat["text_responses"][:10]):
                st.caption(f"\u2022 {resp}")
            if len(stat["text_responses"]) > 10:
                st.caption(f"... and {len(stat['text_responses']) - 10} more")

        st.divider()

    # Show individual responses
    with st.expander(t("view_responses")):
        for resp in responses:
            st.write(f"**{resp['display_name']}** ({resp['email']})")
            st.caption(f"{resp['completed_at']}")
            answers = get_survey_response_answers(resp["id"])
            for ans in answers:
                st.write(f"\u2022 {ans['question_text']}: {ans['answer_text'] or ', '.join(ans['answer_options'])}")
            st.divider()


def _show_pending_approvals():
    """Show users pending approval for knower or knowter access."""
    pending = get_users_pending_approval()

    if not pending:
        st.info(t("no_users"))
        return

    st.write(f"**{len(pending)} {t('users_pending_approval')}**")

    for user in pending:
        with st.container(border=True):
            col_info, col_action = st.columns([3, 1])
            with col_info:
                st.write(f"**{user['display_name']}**")
                st.caption(user["email"])
                requested = user["requested_role"]
                role_label = t(f"global_role_{requested}")
                st.caption(f"\u2192 {role_label}")
                if user["survey_date"]:
                    st.caption(f"\U0001f4c5 {user['survey_date']}")
            with col_action:
                if st.button(t("approve_access"), key=f"approve_{user['user_id']}", type="primary"):
                    if user["requested_role"] == "knower":
                        approve_knower_access(user["user_id"])
                    else:
                        approve_knowter_access(user["user_id"])
                    st.success(t("access_approved"))
                    st.rerun()


def _show_survey_users():
    """Show users with survey deadlines."""
    users = get_users_needing_survey()
    overdue = get_users_with_overdue_surveys()

    if overdue:
        st.subheader(f"\u26a0\ufe0f {t('overdue')} ({len(overdue)})")
        for user in overdue:
            with st.container(border=True):
                col_info, col_action = st.columns([3, 1])
                with col_info:
                    st.write(f"**{user['display_name']}**")
                    st.caption(user["email"])
                    st.caption(f"\u23f0 {user['deadline']}")
                with col_action:
                    if st.button(t("revoke_access"), key=f"revoke_{user['user_id']}", type="primary"):
                        revoke_survey_based_access(user["user_id"])
                        st.success(t("access_revoked"))
                        st.rerun()

    if users:
        st.subheader(t("users_needing_survey"))
        for user in users:
            # Skip if already in overdue list
            if any(o["user_id"] == user["user_id"] for o in overdue):
                continue
            with st.container(border=True):
                st.write(f"**{user['display_name']}**")
                st.caption(user["email"])
                if user["deadline"]:
                    from datetime import datetime
                    try:
                        deadline = datetime.fromisoformat(user["deadline"])
                        days = (deadline - datetime.now()).days
                        if days <= 7:
                            st.warning(t("days_remaining", n=days))
                        else:
                            st.caption(f"\U0001f4c5 {t('survey_deadline')}: {user['deadline'][:10]}")
                    except (ValueError, TypeError):
                        st.caption(f"\U0001f4c5 {user['deadline']}")
    elif not overdue:
        st.info(t("no_users"))
