import streamlit as st
from translations import t
from db import get_user_profile, update_user_profile, delete_user_account
from auth import _get_avatar_html


def show_profile():
    """Show profile settings page."""
    st.header(t("profile_header"))

    profile = get_user_profile(st.session_state.user_id)
    current_name = profile["display_name"] or st.session_state.get("display_name", st.session_state.get("username", ""))
    current_avatar = profile["avatar"]

    if current_avatar:
        st.image(current_avatar, width=120)
    else:
        st.markdown(_get_avatar_html(None, size=120), unsafe_allow_html=True)

    st.divider()

    display_name = st.text_input(t("display_name"), value=current_name, key="profile_name_input")

    uploaded_file = st.file_uploader(
        t("upload_photo"),
        type=["png", "jpg", "jpeg"],
        key="profile_avatar_upload",
    )

    if st.button(t("save"), type="primary"):
        avatar_data = None
        if uploaded_file is not None:
            avatar_data = uploaded_file.read()
        elif current_avatar:
            avatar_data = current_avatar

        if avatar_data is not None:
            update_user_profile(st.session_state.user_id, display_name, avatar_data)
        else:
            update_user_profile(st.session_state.user_id, display_name)

        st.session_state.display_name = display_name
        st.session_state.avatar_bytes = avatar_data
        st.success(t("profile_updated"))
        st.session_state.page = st.session_state.get("prev_page", "Tests")
        st.rerun()

    # Delete account section
    st.divider()
    with st.expander(f"\u26a0\ufe0f {t('delete_account')}", expanded=False):
        st.warning(t("delete_account_warning"))
        user_email = st.session_state.get("username", "")

        confirm_email = st.text_input(
            t("delete_account_confirm_email"),
            key="delete_account_confirm_input",
            placeholder=user_email,
        )
        email_matches = confirm_email.lower().strip() == user_email.lower().strip()
        if st.button(t("delete_account"), type="primary", disabled=not email_matches):
            delete_user_account(st.session_state.user_id)
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.logout()
            st.rerun()
