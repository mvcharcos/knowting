import streamlit as st
from translations import t
from auth import _is_logged_in, _is_global_admin, _can_create_tests
from helpers import (
    _render_test_card, _show_import_test_inline, _lang_display,
    _toggle_bulk_test, _get_test_export_data,
)
from db import (
    get_all_tests, get_tests_performance, get_favorite_tests,
    get_shared_tests, get_pending_invitations,
    accept_test_invitation, decline_test_invitation,
    delete_test, get_user_role_for_test,
)


def show_test_catalog():
    """Show a searchable catalog of available tests."""
    user_id = st.session_state.get("user_id")
    all_tests = get_all_tests(user_id)
    logged_in = _is_logged_in()

    # Get user's performance for all tests
    test_performance = {}
    if logged_in:
        test_ids = [tt["id"] for tt in all_tests]
        test_performance = get_tests_performance(user_id, test_ids)

    st.header(t("available_tests"))

    # --- Pending Test Invitations Section ---
    if logged_in:
        invitations = get_pending_invitations(user_id)
        test_invitations = invitations["tests"]

        if test_invitations:
            with st.expander(f"📩 {t('pending_invitations')} ({len(test_invitations)})", expanded=True):
                for inv in test_invitations:
                    with st.container(border=True):
                        col_info, col_actions = st.columns([3, 1])
                        with col_info:
                            st.markdown(f"**{t('test_invitation')}:** {inv['title']}")
                            st.caption(f"{t('invited_by', name=inv['inviter_name'])} {t('invited_as', role=inv['role'])}")
                        with col_actions:
                            c1, c2 = st.columns(2)
                            if c1.button("✓", key=f"accept_test_{inv['test_id']}", help=t("accept_invitation")):
                                accept_test_invitation(inv['test_id'], user_id)
                                st.success(t("invitation_accepted"))
                                st.rerun()
                            if c2.button("✕", key=f"decline_test_{inv['test_id']}", help=t("decline_invitation")):
                                decline_test_invitation(inv['test_id'], user_id)
                                st.info(t("invitation_declined"))
                                st.rerun()
            st.divider()

    # Show import success message if any
    if "import_success" in st.session_state:
        st.success(st.session_state.import_success)
        del st.session_state.import_success

    # Admin buttons: create, import, bulk delete
    bulk_delete_mode = False
    if _is_global_admin():
        col_create, col_import, col_bulk = st.columns([1, 1, 1])
        with col_create:
            if _can_create_tests():
                if st.button(t("create_test"), type="secondary", width="stretch"):
                    st.session_state.page = "Crear Test"
                    st.rerun()
        with col_import:
            if st.button(t("import_test"), type="secondary", width="stretch"):
                st.session_state["_show_import_test"] = not st.session_state.get("_show_import_test", False)
                st.rerun()
        with col_bulk:
            if all_tests:
                bulk_delete_mode = st.toggle(t("bulk_delete_mode"), key="test_bulk_delete_mode")
                if bulk_delete_mode:
                    if "bulk_delete_tests" not in st.session_state:
                        st.session_state.bulk_delete_tests = set()
    elif logged_in and _can_create_tests():
        if st.button(t("create_test"), type="secondary"):
            st.session_state.page = "Crear Test"
            st.rerun()

    # Show inline import test form when toggled
    if st.session_state.get("_show_import_test", False):
        with st.container(border=True):
            _show_import_test_inline()

    if not all_tests:
        st.info(t("no_tests"))
        return

    col_search, col_lang = st.columns([3, 1])
    with col_search:
        search = st.text_input(t("search_test"), placeholder=t("search_placeholder"), key="test_search")
    with col_lang:
        # Build language filter options from available tests
        available_langs = sorted({tt["language"] for tt in all_tests if tt.get("language")})
        lang_options = [""] + available_langs
        lang_labels = {code: _lang_display(code) if code else t("all_languages") for code in lang_options}
        selected_lang = st.selectbox(
            t("language"), options=lang_options,
            format_func=lambda x: lang_labels[x],
            key="test_lang_filter",
        )

    favorites = get_favorite_tests(st.session_state.user_id) if logged_in else set()

    # Show bulk delete controls below the create button
    if bulk_delete_mode:
        selected_count = len(st.session_state.get("bulk_delete_tests", set()))
        col_info, col_del = st.columns([3, 1])
        with col_info:
            st.info(t("selected_items", n=selected_count))
        with col_del:
            if st.button(t("delete_selected"), type="primary", disabled=selected_count == 0, width="stretch"):
                for test_id in st.session_state.bulk_delete_tests:
                    delete_test(test_id)
                st.session_state.bulk_delete_tests = set()
                st.success(t("tests_deleted", n=selected_count))
                st.rerun()

    filtered_tests = [
        tt for tt in all_tests
        if (not search or search.lower() in tt["title"].lower())
        and (not selected_lang or tt.get("language") == selected_lang)
    ]

    if not filtered_tests:
        st.info(t("no_tests_found"))
        return

    fav_tests = [tt for tt in filtered_tests if tt["id"] in favorites]
    other_tests = [tt for tt in filtered_tests if tt["id"] not in favorites]

    # Shared with me
    shared_tests = []
    shared_test_ids = set()
    if logged_in:
        shared_tests = get_shared_tests(st.session_state.user_id)
        shared_test_ids = {tt["id"] for tt in shared_tests}

    # Build set of test IDs the user has access to (owner or collaborator)
    accessible_ids = shared_test_ids.copy()
    if logged_in:
        accessible_ids |= {tt["id"] for tt in all_tests if tt.get("owner_id") == st.session_state.user_id}

    def _has_access(tt):
        vis = tt.get("visibility", "public")
        if vis in ("public", "restricted"):
            return True
        return tt["id"] in accessible_ids

    def _can_edit(tt):
        """Check if current user can edit this test."""
        if not logged_in:
            return False
        if _is_global_admin():
            return True
        if tt.get("owner_id") == st.session_state.user_id:
            return True
        # Check if user has reviewer/admin role on this test
        role = get_user_role_for_test(tt["id"], st.session_state.user_id)
        return role in ("reviewer", "admin")

    if fav_tests:
        st.subheader(t("favorites"))
        for test in fav_tests:
            _render_test_card(test, favorites, prefix="fav_", has_access=_has_access(test), bulk_delete_mode=bulk_delete_mode, performance=test_performance, can_edit=_can_edit(test))

    if shared_tests:
        st.subheader(t("shared_with_me"))
        for test in shared_tests:
            if test["id"] not in {tt["id"] for tt in fav_tests}:
                # For shared tests, check the role they have
                can_edit_shared = test.get("role") in ("reviewer", "admin") or _is_global_admin()
                _render_test_card(test, favorites, prefix="shared_", has_access=True, bulk_delete_mode=bulk_delete_mode, performance=test_performance, can_edit=can_edit_shared)

    other_tests = [tt for tt in other_tests if tt["id"] not in shared_test_ids]
    if other_tests:
        if fav_tests or shared_tests:
            st.subheader(t("all_tests"))
        for test in other_tests:
            _render_test_card(test, favorites, has_access=_has_access(test), bulk_delete_mode=bulk_delete_mode, performance=test_performance, can_edit=_can_edit(test))
