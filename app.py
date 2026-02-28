import streamlit as st
from translations import t
from db import (
    init_db, set_user_global_role_by_email,
    get_active_initial_survey, get_active_periodic_survey,
    get_pending_approval_count,
)
from auth import (
    _is_logged_in, _try_login, _load_profile_to_session,
    _get_global_role, _is_global_admin, _is_visitor, _can_create_tests,
    _is_pending_approval, _needs_survey, _check_survey_deadline,
)
from helpers import UI_LANGUAGES, UI_LANG_LABELS

from views.home import (
    show_home_page, show_privacy_policy, show_terms_and_conditions,
    show_choose_access_type, show_terms_acceptance,
)
from views.tests import show_test_catalog
from views.test_config import show_test_config, show_quiz
from views.test_editor import show_create_test, show_test_editor
from views.dashboard import show_dashboard, _show_visitor_preview_dashboard
from views.programs import (
    show_programs, show_create_program, show_program_editor,
    show_program_config, _show_visitor_preview_programs,
)
from views.admin import show_admin_panel, show_survey_page, show_admin_surveys
from views.profile import show_profile
from views.research import show_research

init_db()

# Set initial admin user (only this one is hardcoded, others managed via admin panel)
set_user_global_role_by_email("mcharcos@socib.es", "admin")


def main():
    st.set_page_config(page_title="Knowting Club", page_icon="📚")

    _try_login()

    # Check if there's a pending registration (new user needs to accept terms)
    if st.session_state.get("pending_registration"):
        show_terms_acceptance()
        return

    if _is_logged_in():
        _load_profile_to_session()

    if "page" not in st.session_state:
        st.session_state.page = "Home"

    logged_in = _is_logged_in()
    _is_tester_user = logged_in and _get_global_role() == "tester"
    _show_full_ui = not _is_tester_user  # everyone sees full UI except testers

    # Redirect tester users away from Home to Tests
    if st.session_state.page == "Home" and _is_tester_user:
        st.session_state.page = "Tests"

    # Top bar: title + avatar/login (hidden for non-logged-in and tester users)
    if _show_full_ui:
        col_title, col_avatar = st.columns([6, 1])
        with col_title:
            st.title("Knowting Club")
            st.subheader(f"*{t('tagline')}*")
    else:
        col_avatar = st.columns([1])[0]
    with col_avatar:
        if logged_in:
            avatar_bytes = st.session_state.get("avatar_bytes")
            display_name = st.session_state.get("display_name", st.session_state.username)
            with st.popover("👤"):
                if avatar_bytes:
                    st.image(avatar_bytes, width=60)
                st.write(f"**{display_name}**")
                st.divider()
                if st.button(t("profile"), key="menu_profile", width="stretch"):
                    st.session_state.prev_page = st.session_state.page
                    st.session_state.page = "Perfil"
                    st.rerun()
                if st.button(t("logout"), key="menu_logout", width="stretch"):
                    for key in list(st.session_state.keys()):
                        del st.session_state[key]
                    st.logout()
                    st.rerun()
        else:
            col_login, col_privacy, col_terms = st.columns([1, 1, 1])
            with col_login:
                st.button("🔑", on_click=st.login, help=t("login_with_google"))
            with col_privacy:
                if st.button("🔒", key="header_privacy", help=t("privacy_policy")):
                    st.session_state.page = "Privacy Policy"
                    st.rerun()
            with col_terms:
                if st.button("📜", key="header_terms", help=t("terms_and_conditions")):
                    st.session_state.page = "Terms"
                    st.rerun()

    # Sidebar navigation
    is_tester = logged_in and _get_global_role() == "tester"
    show_full_ui = not is_tester  # everyone sees full UI except testers
    with st.sidebar:
        # Logo - hidden for tester users only
        if show_full_ui:
            st.image("assets/KnowtingLogo.png", use_container_width=True)
            if logged_in:
                role_key = f"global_role_{_get_global_role()}"
                role_label = t(role_key)
                st.caption(f"<div style='text-align:center;'>{role_label}</div>", unsafe_allow_html=True)

        # Language toggle - hidden for tester users only
        if show_full_ui:
            current_lang = st.session_state.get("lang", "es")
            current_idx = UI_LANGUAGES.index(current_lang) if current_lang in UI_LANGUAGES else 0
            selected_ui_lang = st.selectbox(
                "🌐", options=UI_LANGUAGES,
                index=current_idx,
                format_func=lambda x: UI_LANG_LABELS.get(x, x),
                key="lang_toggle",
                label_visibility="collapsed",
            )
            if selected_ui_lang != current_lang:
                st.session_state.lang = selected_ui_lang
                st.rerun()

        st.markdown("---")
        nav_items = []
        # Home - hidden for non-logged-in and tester users
        if show_full_ui:
            nav_items.append(("🏠", "Home", t("home")))
        nav_items.append(("📝", "Tests", t("tests")))
        if show_full_ui:
            nav_items.append(("📊", "Dashboard", t("dashboard")))
            nav_items.append(("📚", "Cursos", t("courses")))
        if logged_in and _is_global_admin():
            pending_count = get_pending_approval_count()
            surveys_label = t("surveys")
            if pending_count > 0:
                surveys_label = f"{surveys_label} ({pending_count})"
            nav_items.append(("📋", "Surveys", surveys_label))
            nav_items.append(("⚙️", "Admin", t("admin_panel")))
            nav_items.append(("🔬", "Research", t("research")))
        for icon, page_id, display in nav_items:
            is_active = st.session_state.page == page_id
            btn_type = "primary" if is_active else "secondary"
            if st.button(f"{icon}  {display}", key=f"nav_{page_id}", width="stretch", type=btn_type):
                st.session_state.page = page_id
                st.rerun()
        st.markdown("---")

        # Legal links at the bottom of sidebar (icons with tooltips)
        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            if st.button("🔒", key="nav_privacy", help=t("privacy_policy")):
                st.session_state.page = "Privacy Policy"
                st.rerun()
        with col2:
            if st.button("📜", key="nav_terms", help=t("terms_and_conditions")):
                st.session_state.page = "Terms"
                st.rerun()

    if "quiz_started" not in st.session_state:
        st.session_state.quiz_started = False

    # Check for pause time capture query params and route to editor
    if st.query_params.get("capture_t") is not None and st.query_params.get("capture_mat_id") is not None:
        st.session_state.page = "Editar Test"

    # --- Page routing ---
    if logged_in and st.session_state.page == "Perfil":
        show_profile()
    elif st.session_state.page == "Dashboard" and not st.session_state.quiz_started:
        if logged_in and not _is_visitor():
            show_dashboard()
        else:
            _show_visitor_preview_dashboard()
    elif st.session_state.page == "Configurar Test":
        show_test_config()
    elif logged_in and st.session_state.page == "Crear Test" and _can_create_tests():
        show_create_test()
    elif logged_in and st.session_state.page == "Editar Test":
        show_test_editor()
    elif st.session_state.page == "Cursos" and not st.session_state.quiz_started:
        if logged_in and not _is_visitor():
            show_programs()
        else:
            _show_visitor_preview_programs()
    elif logged_in and st.session_state.page == "Crear Curso":
        show_create_program()
    elif logged_in and st.session_state.page == "Editar Curso":
        show_program_editor()
    elif logged_in and st.session_state.page == "Configurar Curso":
        show_program_config()
    elif logged_in and _is_global_admin() and st.session_state.page == "Admin":
        show_admin_panel()
    elif logged_in and _is_global_admin() and st.session_state.page == "Surveys":
        show_admin_surveys()
    elif logged_in and _is_global_admin() and st.session_state.page == "Research":
        show_research()
    elif logged_in and st.session_state.page == "Choose Access Type":
        show_choose_access_type()
    elif logged_in and st.session_state.page == "Take Initial Survey":
        initial_survey = get_active_initial_survey()
        if initial_survey:
            show_survey_page(initial_survey)
        else:
            st.warning(t("no_active_survey"))
            if st.button(t("back")):
                st.session_state.page = "Home"
                st.rerun()
    elif logged_in and st.session_state.page == "Take Periodic Survey":
        pending_survey = st.session_state.get("pending_survey")
        if pending_survey:
            show_survey_page(pending_survey)
        else:
            periodic_survey = get_active_periodic_survey()
            if periodic_survey:
                show_survey_page(periodic_survey)
            else:
                st.warning(t("no_active_survey"))
                if st.button(t("back")):
                    st.session_state.page = "Home"
                    st.rerun()
    elif st.session_state.page == "Home":
        # Show pending approval message or survey deadline warning
        if logged_in:
            if _is_pending_approval():
                st.info(t("pending_approval_message"))
            else:
                deadline_info = _check_survey_deadline()
                if deadline_info and deadline_info.get("warning"):
                    if deadline_info.get("overdue"):
                        st.error(t("survey_deadline_warning", date=deadline_info["deadline"]))
                    else:
                        st.warning(t("survey_deadline_warning", date=deadline_info["deadline"]))
                    # Check if they need to take the survey
                    needs, survey = _needs_survey()
                    if needs and survey:
                        if st.button(t("take_initial_survey"), type="primary"):
                            st.session_state.page = "Take Periodic Survey"
                            st.session_state.pending_survey = survey
                            st.rerun()
        show_home_page()
    elif st.session_state.page == "Privacy Policy":
        show_privacy_policy()
    elif st.session_state.page == "Terms":
        show_terms_and_conditions()
    elif st.session_state.quiz_started:
        show_quiz()
    elif st.session_state.page == "Tests":
        show_test_catalog()
    else:
        show_home_page()


if __name__ == "__main__":
    main()
