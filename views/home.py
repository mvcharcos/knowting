import streamlit as st
from translations import t
from auth import (
    _is_logged_in, _get_global_role, _is_pending_approval, _is_global_admin,
    _can_create_tests, _can_create_programs,
)
from helpers import _read_legal_document
from db import (
    get_or_create_google_user, resolve_collaborator_user_id,
    get_user_survey_status, create_user_survey_status, update_user_survey_status,
    get_pending_approval_count, get_app_stats,
)


def show_home_page():
    """Display the home page with app introduction, video, and plans."""
    st.header(t("home_welcome"))
    st.write(t("home_intro"))

    st.divider()

    # Video section
    st.subheader(f"🎬 {t('home_video_title')}")
    # Different videos for each language
    home_videos = {
        "es": "https://youtu.be/8KRWO8JdyPI",
        "en": "https://youtu.be/kSYJ9CeXnO8",
        "fr": "https://youtu.be/XgYThH36kGM",
        "ca": "https://youtu.be/o71o6B3l9Qg",
    }
    current_lang = st.session_state.get("lang", "es")
    st.video(home_videos.get(current_lang, home_videos["es"]))

    st.divider()

    # App statistics
    stats = get_app_stats()
    col_s1, col_s2, col_s3 = st.columns(3)
    col_s1.metric(t("stat_users"), stats["users"])
    col_s2.metric(t("stat_tests"), stats["tests"])
    col_s3.metric(t("stat_courses"), stats["programs"])

    st.divider()

    # Open Source section
    with st.container(border=True):
        col_icon, col_text = st.columns([0.5, 5])
        with col_icon:
            st.markdown(
                '<a href="https://github.com/mvcharcos/knowting" target="_blank">'
                '<svg height="64" viewBox="0 0 16 16" width="64" style="fill:currentColor;">'
                '<path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38'
                ' 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15'
                '-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51'
                '-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0'
                ' 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2'
                '-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29'
                '.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8'
                'c0-4.42-3.58-8-8-8z"/></svg></a>',
                unsafe_allow_html=True,
            )
        with col_text:
            st.subheader(t("home_opensource_title"))
            st.write(t("home_opensource_text"))
            st.write(t("home_opensource_contribute"))
        st.link_button(
            f"GitHub — {t('home_opensource_btn')}",
            url="https://github.com/mvcharcos/knowting",
            type="secondary",
        )

    st.divider()

    # Plans section
    st.subheader(f"📋 {t('home_plans_title')}")

    logged_in = _is_logged_in()
    current_role = _get_global_role() if logged_in else None
    pending_approval = logged_in and _is_pending_approval()

    col1, col2, col3, col4 = st.columns(4)

    # No-registration plan (no login)
    with col1:
        with st.container(border=True):
            st.markdown(f"### 👁️ {t('home_plan_visitor')}")
            st.caption(t("home_plan_visitor_desc"))
            st.markdown("---")
            st.markdown(f"✅ {t('home_feature_take_public_tests')}")
            st.markdown("&nbsp;")
            st.markdown("&nbsp;")
            st.markdown("&nbsp;")
            st.markdown("&nbsp;")
            st.markdown("&nbsp;")
            st.markdown("&nbsp;")
            st.markdown("---")
            if not logged_in:
                if st.button(t("home_get_started"), key="visitor_start", width="stretch"):
                    st.session_state.page = "Tests"
                    st.rerun()

    # Visitor plan (registered, default)
    with col2:
        is_current = logged_in and current_role == "visitor"
        with st.container(border=True):
            st.markdown(f"### 🔑 {t('global_role_visitor')}")
            st.caption(t("home_plan_visitor_access"))
            if is_current:
                st.success(t("home_current_plan"))
            st.markdown("---")
            st.markdown(f"✅ {t('home_feature_take_public_tests')}")
            st.markdown(f"✅ {t('home_feature_view_materials')}")
            st.markdown(f"👁️ {t('home_feature_track_progress')}")
            st.markdown("&nbsp;")
            st.markdown("&nbsp;")
            st.markdown("&nbsp;")
            st.markdown("&nbsp;")
            st.markdown("---")
            if not logged_in:
                st.button("🔑", on_click=st.login, help=t("login_with_google"), key="visitor_login", width="stretch")

    # Knower plan (upgraded via survey)
    with col3:
        is_current = logged_in and current_role == "knower"
        is_visitor = logged_in and current_role == "visitor"
        with st.container(border=True):
            st.markdown(f"### 🎓 {t('home_plan_knower')}")
            st.caption(t("home_plan_knower_access"))
            if is_current:
                st.success(t("home_current_plan"))
            elif is_visitor and pending_approval:
                st.info(t("pending_admin_review"))
            st.markdown("---")
            st.markdown(f"✅ {t('home_feature_take_public_tests')}")
            st.markdown(f"✅ {t('home_feature_view_materials')}")
            st.markdown(f"✅ {t('home_feature_create_tests')}")
            st.markdown(f"✅ {t('home_feature_upload_materials')}")
            st.markdown(f"✅ {t('home_feature_track_progress')}")
            st.markdown(f"✅ {t('home_feature_invite_collaborators')}")
            st.markdown(f"📋 {t('home_feature_periodic_survey')}")
            st.markdown("---")
            if not logged_in:
                st.button("🔑", on_click=st.login, help=t("login_with_google"), key="knower_login", width="stretch")
            elif is_visitor:
                if _is_pending_approval():
                    st.info(t("pending_admin_review"))
                elif st.session_state.get("_confirm_knower"):
                    st.warning(t("upgrade_survey_warning"))
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button(t("confirm"), key="confirm_knower_yes", type="primary", use_container_width=True):
                            user_id = st.session_state.get("user_id")
                            status = get_user_survey_status(user_id)
                            if not status:
                                create_user_survey_status(user_id, "survey", initial_completed=False, pending_approval=True)
                            else:
                                update_user_survey_status(user_id, pending_approval=True)
                            del st.session_state._confirm_knower
                            st.success(t("access_request_sent"))
                            st.rerun()
                    with c2:
                        if st.button(t("cancel"), key="confirm_knower_no", use_container_width=True):
                            del st.session_state._confirm_knower
                            st.rerun()
                else:
                    if st.button(t("become_knower"), key="become_knower_btn", width="stretch"):
                        st.session_state._confirm_knower = True
                        st.rerun()

    # Knowter plan (upgraded via survey)
    with col4:
        is_current = logged_in and current_role == "knowter"
        is_knower = logged_in and current_role == "knower"
        with st.container(border=True):
            st.markdown(f"### 🚀 {t('home_plan_knowter')}")
            st.caption(t("home_plan_knowter_access"))
            if is_current:
                st.success(t("home_current_plan"))
            elif is_knower and pending_approval:
                st.info(t("pending_admin_review"))
            st.markdown("---")
            st.markdown(f"✅ {t('home_feature_take_public_tests')}")
            st.markdown(f"✅ {t('home_feature_view_materials')}")
            st.markdown(f"✅ {t('home_feature_create_tests')}")
            st.markdown(f"✅ {t('home_feature_upload_materials')}")
            st.markdown(f"✅ {t('home_feature_track_progress')}")
            st.markdown(f"✅ {t('home_feature_invite_collaborators')}")
            st.markdown(f"✅ {t('home_feature_create_courses')}")
            st.markdown(f"✅ {t('home_feature_course_collaborators')}")
            st.markdown(f"✅ {t('home_feature_advanced_visibility')}")
            st.markdown(f"📋 {t('home_feature_periodic_survey')}")
            st.markdown("---")
            if not logged_in:
                st.button("🔑", on_click=st.login, help=t("login_with_google"), key="knowter_login", width="stretch")
            elif is_knower:
                if _is_pending_approval():
                    st.info(t("pending_admin_review"))
                elif st.session_state.get("_confirm_knowter"):
                    st.warning(t("upgrade_survey_warning"))
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button(t("confirm"), key="confirm_knowter_yes", type="primary", use_container_width=True):
                            user_id = st.session_state.get("user_id")
                            status = get_user_survey_status(user_id)
                            if not status:
                                create_user_survey_status(user_id, "survey", initial_completed=False, pending_approval=True)
                            else:
                                update_user_survey_status(user_id, pending_approval=True)
                            del st.session_state._confirm_knowter
                            st.success(t("access_request_sent"))
                            st.rerun()
                    with c2:
                        if st.button(t("cancel"), key="confirm_knowter_no", use_container_width=True):
                            del st.session_state._confirm_knowter
                            st.rerun()
                else:
                    if st.button(t("become_knowter"), key="become_knowter_btn", width="stretch"):
                        st.session_state._confirm_knowter = True
                        st.rerun()


def show_privacy_policy():
    """Display the privacy policy page."""
    if st.button(f"← {t('back')}"):
        st.session_state.page = "Home"
        st.rerun()

    content = _read_legal_document("privacy_policy")
    if content:
        st.markdown(content)
    else:
        st.warning("Privacy policy not available.")


def show_terms_and_conditions():
    """Display the terms and conditions page."""
    if st.button(f"← {t('back')}"):
        st.session_state.page = "Home"
        st.rerun()

    content = _read_legal_document("terms")
    if content:
        st.markdown(content)
    else:
        st.warning("Terms and conditions not available.")


def show_choose_access_type():
    """Redirect to Home — upgrade is now handled directly in the plans section."""
    st.session_state.page = "Home"
    st.rerun()


def show_terms_acceptance():
    """Show the terms and privacy policy acceptance screen for new user registration."""
    pending = st.session_state.get("pending_registration", {})
    if not pending:
        return False

    st.header(t("welcome_new_user"))
    st.write(t("accept_terms_intro"))

    # Show terms and conditions in an expander
    with st.expander(f"📜 {t('terms_and_conditions')}", expanded=False):
        terms_content = _read_legal_document("terms")
        if terms_content:
            st.markdown(terms_content)

    # Show privacy policy in an expander
    with st.expander(f"🔒 {t('privacy_policy')}", expanded=False):
        privacy_content = _read_legal_document("privacy_policy")
        if privacy_content:
            st.markdown(privacy_content)

    st.divider()

    # Acceptance checkboxes
    accept_terms = st.checkbox(t("i_accept_terms"), key="accept_terms_checkbox")
    accept_privacy = st.checkbox(t("i_accept_privacy"), key="accept_privacy_checkbox")

    col1, col2 = st.columns(2)
    with col1:
        if st.button(t("create_account"), type="primary", disabled=not (accept_terms and accept_privacy)):
            if accept_terms and accept_privacy:
                # Create the user account
                email = pending["email"]
                name = pending["name"]
                user_id = get_or_create_google_user(email, name)
                resolve_collaborator_user_id(email, user_id)
                st.session_state.user_id = user_id
                st.session_state.username = email
                st.session_state.display_name = name
                # Clear pending registration
                del st.session_state.pending_registration
                st.rerun()
            else:
                st.warning(t("must_accept_both"))
    with col2:
        if st.button(t("cancel_registration"), type="secondary"):
            # Clear pending registration and logout
            del st.session_state.pending_registration
            st.logout()
            st.rerun()

    return True
