import streamlit as st
from translations import t
from auth import (
    _is_logged_in, _is_global_admin, _can_create_programs, _is_visitor,
    _get_global_role, _needs_survey_for_feature, _show_survey_required_message,
)
from helpers import (
    _render_test_card, _get_test_export_data, _get_program_export_data,
    _toggle_bulk_program, _lang_display, LANGUAGE_OPTIONS, LANGUAGE_KEYS,
    select_balanced_questions, shuffle_question_options,
)
from db import (
    get_all_programs, get_shared_programs, get_program, get_program_tests,
    get_program_collaborators, get_program_questions, get_program_tags,
    create_program, update_program, delete_program,
    add_test_to_program, remove_test_from_program, update_program_test_visibility,
    get_visibility_options_for_test, get_effective_visibility,
    add_program_collaborator, remove_program_collaborator, update_program_collaborator_role,
    get_pending_invitations, accept_program_invitation, decline_program_invitation,
    get_all_tests, get_tests_performance,
    get_user_role_for_program, get_user_role_for_test,
    get_test, has_direct_test_access,
    get_topic_statistics, get_question_stats,
    create_session,
)


def _show_visitor_preview_programs():
    """Show a greyed-out preview of the courses page for visitor users."""
    st.header(t("courses_header"))
    st.info(f"\U0001f441\ufe0f {t('visitor_feature_preview')}")

    st.markdown(
        '<style>.visitor-preview { opacity: 0.35; pointer-events: none; }</style>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="visitor-preview">', unsafe_allow_html=True)

    # --- Fake course cards ---
    st.subheader(t("my_courses"))
    for i in range(2):
        with st.container(border=True):
            st.markdown(f"### \U0001f4da \u2014\u2014\u2014\u2014\u2014\u2014\u2014")
            st.caption("\u2014 \u2014 \u2014")

    st.markdown('</div>', unsafe_allow_html=True)

    st.divider()
    st.markdown(f"### {t('visitor_upgrade_message')}")
    if st.button(t("become_knower"), key="visitor_upgrade_courses", type="primary"):
        st.session_state.page = "Home"
        st.rerun()


def _render_program_card(prog, user_id, has_access=True, prefix="", bulk_delete_mode=False):
    """Render a single program card with select/edit/export buttons."""
    is_owner = prog.get("owner_id") == user_id
    prog_role = get_user_role_for_program(prog["id"], user_id) if not is_owner else None
    can_edit = _is_global_admin() or is_owner or prog_role in ("reviewer", "admin")

    with st.container(border=True):
        if bulk_delete_mode:
            col_check, col_info, col_btn = st.columns([0.5, 4, 1.5])
            with col_check:
                # Initialize set if needed
                if "bulk_delete_programs" not in st.session_state:
                    st.session_state.bulk_delete_programs = set()
                is_selected = prog["id"] in st.session_state.bulk_delete_programs
                st.checkbox("", value=is_selected, key=f"{prefix}bulk_select_prog_{prog['id']}",
                           label_visibility="collapsed", on_change=_toggle_bulk_program, args=(prog["id"],))
        else:
            col_info, col_btn = st.columns([3.5, 2])
        with col_info:
            title_display = prog["title"]
            if prog.get("visibility") == "private" and not has_access:
                title_display = "\U0001f512 " + title_display
            st.subheader(title_display)
            if prog.get("description"):
                st.write(prog["description"])
            st.caption(t("n_tests", n=prog['test_count']))
        with col_btn:
            # Show buttons in a row: Select, Edit (if can_edit), Export (if can_edit)
            btn_cols = st.columns(3 if can_edit else 1)
            col_idx = 0
            with btn_cols[col_idx]:
                if has_access:
                    if st.button("\u25b6\ufe0f", key=f"{prefix}prog_sel_{prog['id']}", width="stretch", help=t("select")):
                        st.session_state.selected_program = prog["id"]
                        st.session_state.page = "Configurar Curso"
                        st.rerun()
                else:
                    st.button("\u25b6\ufe0f", key=f"{prefix}prog_sel_{prog['id']}", width="stretch", disabled=True, help=t("select"))
            if can_edit:
                col_idx += 1
                with btn_cols[col_idx]:
                    if st.button("\u270f\ufe0f", key=f"{prefix}prog_edit_{prog['id']}", width="stretch", help=t("edit_course")):
                        st.session_state.editing_program_id = prog["id"]
                        st.session_state.page = "Editar Curso"
                        st.rerun()
                col_idx += 1
                with btn_cols[col_idx]:
                    export_data, export_title = _get_program_export_data(prog["id"])
                    st.download_button(
                        "\u2b07\ufe0f",
                        data=export_data,
                        file_name=f"{export_title}.json",
                        mime="application/json",
                        key=f"{prefix}prog_export_{prog['id']}",
                        width="stretch",
                        help=t("export_course"),
                    )


def show_programs():
    """Show the program catalog."""
    user_id = st.session_state.user_id
    programs = get_all_programs(user_id)
    shared_programs = get_shared_programs(user_id)
    shared_prog_ids = {p["id"] for p in shared_programs}

    st.header(t("courses_header"))

    # --- Pending Program Invitations Section ---
    invitations = get_pending_invitations(user_id)
    program_invitations = invitations["programs"]

    if program_invitations:
        with st.expander(f"\U0001f4e9 {t('pending_invitations')} ({len(program_invitations)})", expanded=True):
            for inv in program_invitations:
                with st.container(border=True):
                    col_info, col_actions = st.columns([3, 1])
                    with col_info:
                        st.markdown(f"**{t('program_invitation')}:** {inv['title']}")
                        st.caption(f"{t('invited_by', name=inv['inviter_name'])} {t('invited_as', role=inv['role'])}")
                    with col_actions:
                        c1, c2 = st.columns(2)
                        if c1.button("\u2713", key=f"accept_prog_cat_{inv['program_id']}", help=t("accept_invitation")):
                            accept_program_invitation(inv['program_id'], user_id)
                            st.success(t("invitation_accepted"))
                            st.rerun()
                        if c2.button("\u2715", key=f"decline_prog_cat_{inv['program_id']}", help=t("decline_invitation")):
                            decline_program_invitation(inv['program_id'], user_id)
                            st.info(t("invitation_declined"))
                            st.rerun()
        st.divider()

    # Admin bulk delete mode and create button
    bulk_delete_mode = False
    if _is_global_admin():
        col_create, col_bulk = st.columns([1, 1])
        with col_create:
            if st.button(t("create_course"), type="secondary", width="stretch"):
                st.session_state.page = "Crear Curso"
                st.rerun()
        with col_bulk:
            bulk_delete_mode = st.toggle(t("bulk_delete_mode"), key="prog_bulk_delete_mode")
            if bulk_delete_mode:
                if "bulk_delete_programs" not in st.session_state:
                    st.session_state.bulk_delete_programs = set()
    elif _can_create_programs():
        if st.button(t("create_course"), type="secondary"):
            st.session_state.page = "Crear Curso"
            st.rerun()

    # Show bulk delete controls below the create button
    if bulk_delete_mode:
        selected_count = len(st.session_state.get("bulk_delete_programs", set()))
        col_info, col_del = st.columns([3, 1])
        with col_info:
            st.info(t("selected_items", n=selected_count))
        with col_del:
            if st.button(t("delete_selected"), type="primary", disabled=selected_count == 0, width="stretch"):
                for prog_id in st.session_state.bulk_delete_programs:
                    delete_program(prog_id)
                st.session_state.bulk_delete_programs = set()
                st.success(t("courses_deleted", n=selected_count))
                st.rerun()

    # Build accessible set
    accessible_ids = shared_prog_ids | {p["id"] for p in programs if p.get("owner_id") == user_id}

    def _prog_has_access(p):
        if p.get("visibility", "public") in ("public", "restricted"):
            return True
        return p["id"] in accessible_ids

    my_progs = [p for p in programs if p.get("owner_id") == user_id]
    other_progs = [p for p in programs if p.get("owner_id") != user_id and p["id"] not in shared_prog_ids]

    if my_progs:
        st.subheader(t("my_courses"))
        for prog in my_progs:
            _render_program_card(prog, user_id, has_access=True, prefix="my_", bulk_delete_mode=bulk_delete_mode)

    if shared_programs:
        st.subheader(t("shared_courses"))
        for prog in shared_programs:
            _render_program_card(prog, user_id, has_access=True, prefix="shared_", bulk_delete_mode=bulk_delete_mode)

    if other_progs:
        if my_progs or shared_programs:
            st.subheader(t("all_courses"))
        for prog in other_progs:
            _render_program_card(prog, user_id, has_access=_prog_has_access(prog), bulk_delete_mode=bulk_delete_mode)

    if not programs and not shared_programs:
        st.info(t("no_courses"))


def show_create_program():
    """Show create program form."""
    # Only premium and admin users can create programs
    if not _can_create_programs():
        st.error(t("no_permission"))
        if st.button(t("back_to_courses")):
            st.session_state.page = "Cursos"
            st.rerun()
        return

    # Check if survey-based knowter needs to complete a survey
    needs_survey, survey_type, survey = _needs_survey_for_feature()
    if needs_survey:
        st.header(t("create_new_course"))
        _show_survey_required_message(survey_type, survey, "Crear Curso")
        return

    st.header(t("create_new_course"))

    if st.button(t("back")):
        st.session_state.page = "Cursos"
        st.rerun()

    title = st.text_input(t("course_title"), key="new_prog_title")
    description = st.text_area(t("description"), key="new_prog_desc")

    if st.button(t("create_course_btn"), type="primary"):
        if not title.strip():
            st.warning(t("title_required"))
        else:
            program_id = create_program(st.session_state.user_id, title.strip(), description.strip())
            st.session_state.editing_program_id = program_id
            st.session_state.page = "Editar Curso"
            st.rerun()


def show_program_editor():
    """Show program editor page."""
    # Check if survey-based knowter needs to complete a survey
    needs_survey, survey_type, survey = _needs_survey_for_feature()
    if needs_survey:
        st.header(t("edit_course"))
        _show_survey_required_message(survey_type, survey, "Editar Curso")
        return

    program_id = st.session_state.get("editing_program_id")
    if not program_id:
        st.session_state.page = "Cursos"
        st.rerun()
        return

    prog = get_program(program_id)
    if not prog:
        st.error(t("course_not_found"))
        return

    user_id = st.session_state.get("user_id")
    is_owner = prog["owner_id"] == user_id
    if _is_global_admin():
        prog_role = "owner"  # Global admins have full access to all programs
    elif is_owner:
        prog_role = "owner"
    else:
        prog_role = get_user_role_for_program(program_id, user_id)
    if not prog_role:
        st.error(t("no_permission"))
        return
    read_only = prog_role in ("guest", "student")
    meta_disabled = prog_role not in ("owner", "admin")

    st.header(t("edit_colon", name=prog['title']))

    if st.button(t("back")):
        if "editing_program_id" in st.session_state:
            del st.session_state.editing_program_id
        st.session_state.page = "Cursos"
        st.rerun()

    # --- Metadata ---
    st.subheader(t("course_info"))
    new_title = st.text_input(t("title"), value=prog["title"], key="edit_prog_title", disabled=meta_disabled)
    new_desc = st.text_area(t("description"), value=prog["description"] or "", key="edit_prog_desc", disabled=meta_disabled)

    visibility_options = ["public", "restricted", "private", "hidden"]
    visibility_labels = {
        "public": t("visibility_public"),
        "restricted": t("visibility_restricted"),
        "private": t("visibility_private"),
        "hidden": t("visibility_hidden"),
    }
    current_vis = prog.get("visibility", "public")
    current_vis_index = visibility_options.index(current_vis) if current_vis in visibility_options else 0
    new_visibility = st.selectbox(
        t("visibility"), options=visibility_options,
        index=current_vis_index,
        format_func=lambda x: visibility_labels[x],
        key="edit_prog_visibility",
        disabled=meta_disabled,
    )

    if not meta_disabled:
        if st.button(t("save_info"), type="primary"):
            if not new_title.strip():
                st.warning(t("title_required"))
            else:
                update_program(program_id, new_title.strip(), new_desc.strip(), new_visibility)
                st.success(t("info_updated"))
                st.rerun()

    st.divider()

    # --- Tests in program ---
    st.subheader(t("tests_included"))

    # Visibility labels for program access
    visibility_labels = {
        "public": t("visibility_public"),
        "restricted": t("visibility_restricted"),
        "private": t("visibility_private"),
        "hidden": t("visibility_hidden"),
    }

    prog_tests = get_program_tests(program_id)
    if prog_tests:
        for pt in prog_tests:
            if not meta_disabled:
                col_info, col_vis, col_rm = st.columns([3, 2, 0.5])
                with col_info:
                    st.write(f"**{pt['title']}** ({t('n_questions', n=pt['question_count'])})")
                with col_vis:
                    # Get available visibility options based on test's base visibility
                    test_visibility = pt.get("test_visibility", "public")
                    available_options = get_visibility_options_for_test(test_visibility)
                    current_visibility = pt.get("program_visibility", test_visibility)
                    # Ensure current visibility is in available options
                    if current_visibility not in available_options:
                        current_visibility = available_options[0]
                    current_idx = available_options.index(current_visibility)
                    new_visibility = st.selectbox(
                        t("course_visibility"),
                        options=available_options,
                        index=current_idx,
                        format_func=lambda x: visibility_labels.get(x, x),
                        key=f"prog_visibility_{pt['id']}",
                        label_visibility="collapsed",
                    )
                    if new_visibility != current_visibility:
                        update_program_test_visibility(program_id, pt["id"], new_visibility)
                        st.rerun()
                with col_rm:
                    if st.button("\U0001f5d1\ufe0f", key=f"rm_pt_{pt['id']}"):
                        remove_test_from_program(program_id, pt["id"])
                        st.rerun()
            else:
                visibility_label = visibility_labels.get(pt.get("program_visibility", "public"), t("visibility_public"))
                st.write(f"**{pt['title']}** ({t('n_questions', n=pt['question_count'])}) - {visibility_label}")
    else:
        st.info(t("no_tests_in_course"))

    # Add test (owner and admin only)
    if not meta_disabled:
        all_tests = get_all_tests(st.session_state.user_id)
        current_test_ids = {pt["id"] for pt in prog_tests}
        # Only show tests the user has admin or reviewer access to
        available_tests = []
        for tt in all_tests:
            if tt["id"] in current_test_ids:
                continue
            # Check if user is owner or has admin/reviewer role
            if tt.get("owner_id") == st.session_state.user_id:
                available_tests.append(tt)
            else:
                user_role = get_user_role_for_test(tt["id"], st.session_state.user_id)
                if user_role in ("admin", "reviewer"):
                    available_tests.append(tt)

        if available_tests:
            st.write(t("add_test_label"))
            # Create a dict mapping test_id to test info for visibility lookup
            test_info_map = {tt["id"]: tt for tt in available_tests}
            col_test, col_vis_add = st.columns([3, 2])
            with col_test:
                test_options = {tt["id"]: f"{tt['title']} ({t('n_questions_abbrev', n=tt['question_count'])})" for tt in available_tests}
                selected_test_id = st.selectbox(
                    t("test_label"), options=list(test_options.keys()),
                    format_func=lambda x: test_options[x],
                    key="add_prog_test",
                    label_visibility="collapsed",
                )
            with col_vis_add:
                # Get visibility options based on selected test's base visibility
                selected_test_visibility = test_info_map[selected_test_id].get("visibility", "public")
                add_visibility_options = get_visibility_options_for_test(selected_test_visibility)
                add_program_visibility = st.selectbox(
                    t("course_visibility"),
                    options=add_visibility_options,
                    index=0,
                    format_func=lambda x: visibility_labels.get(x, x),
                    key="add_prog_test_visibility",
                    label_visibility="collapsed",
                )
            if st.button(t("add_test_btn")):
                add_test_to_program(program_id, selected_test_id, add_program_visibility)
                st.rerun()

    st.divider()

    # --- Collaborators (owner and admin only) ---
    if prog_role in ("owner", "admin"):
        st.subheader(t("collaborators"))
        collabs = get_program_collaborators(program_id)
        if collabs:
            for c in collabs:
                col_email, col_status, col_role, col_del = st.columns([2.5, 1, 2, 0.5])
                with col_email:
                    st.write(c["email"])
                with col_status:
                    status = c.get("status", "accepted")
                    if status == "pending":
                        st.caption(f"\u23f3 {t('status_pending')}")
                    else:
                        st.caption(f"\u2713 {t('status_accepted')}")
                with col_role:
                    role_options = ["student", "guest", "reviewer", "admin"]
                    role_labels = {"student": t("role_student"), "guest": t("role_guest"), "reviewer": t("role_reviewer"), "admin": t("role_admin")}
                    new_role = st.selectbox(
                        t("role_label"), options=role_options, index=role_options.index(c["role"]),
                        format_func=lambda x: role_labels[x], key=f"prog_collab_role_{c['id']}",
                    )
                    if new_role != c["role"]:
                        update_program_collaborator_role(program_id, c["email"], new_role)
                        st.rerun()
                with col_del:
                    if st.button("\U0001f5d1\ufe0f", key=f"del_prog_collab_{c['id']}"):
                        remove_program_collaborator(program_id, c["email"])
                        st.success(t("collaborator_removed"))
                        st.rerun()

        st.write(t("invite_user"))
        col_inv_email, col_inv_role, col_inv_btn = st.columns([3, 2, 1])
        with col_inv_email:
            inv_email = st.text_input(t("email_placeholder"), key="prog_invite_email", label_visibility="collapsed", placeholder=t("email_placeholder"))
        with col_inv_role:
            inv_role_options = ["student", "guest", "reviewer", "admin"]
            inv_role_labels = {"student": t("role_student"), "guest": t("role_guest"), "reviewer": t("role_reviewer"), "admin": t("role_admin")}
            inv_role = st.selectbox(t("role_label"), options=inv_role_options, format_func=lambda x: inv_role_labels[x], key="prog_invite_role", label_visibility="collapsed")
        with col_inv_btn:
            if st.button(t("invite_btn"), key="prog_invite_btn", type="secondary"):
                if not inv_email.strip():
                    st.warning(t("email_required"))
                elif inv_email.strip() == st.session_state.get("username"):
                    st.warning(t("cannot_invite_self"))
                else:
                    add_program_collaborator(program_id, inv_email.strip(), inv_role)
                    st.success(t("invitation_sent"))
                    st.rerun()

    st.divider()

    # --- Delete program (owner only) ---
    if prog_role == "owner":
        st.subheader(t("danger_zone"))
        if st.button(t("delete_course"), type="secondary"):
            st.session_state[f"confirm_delete_prog_{program_id}"] = True

        if st.session_state.get(f"confirm_delete_prog_{program_id}"):
            st.warning(t("confirm_delete"))
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button(t("yes_delete"), key="prog_del_yes", type="primary"):
                    delete_program(program_id)
                    if "editing_program_id" in st.session_state:
                        del st.session_state.editing_program_id
                    st.session_state.page = "Cursos"
                    st.rerun()
            with col_no:
                if st.button(t("cancel"), key="prog_del_no"):
                    del st.session_state[f"confirm_delete_prog_{program_id}"]
                    st.rerun()


def show_program_config():
    """Show configuration for a program before starting quiz."""
    program_id = st.session_state.get("selected_program")
    if not program_id:
        st.session_state.page = "Cursos"
        st.rerun()
        return

    prog = get_program(program_id)
    if not prog:
        st.error(t("course_not_found"))
        return

    # Access control for private/hidden programs (restricted and public are open to everyone)
    visibility = prog.get("visibility", "public")
    if visibility not in ("public", "restricted"):
        logged_in_uid = st.session_state.get("user_id")
        has_prog_access = (
            logged_in_uid and (
                prog["owner_id"] == logged_in_uid
                or get_user_role_for_program(program_id, logged_in_uid) is not None
            )
        )
        if not has_prog_access:
            st.error(t("course_private_no_access"))
            if st.button(t("back_to_courses")):
                if "selected_program" in st.session_state:
                    del st.session_state.selected_program
                st.session_state.page = "Cursos"
                st.rerun()
            return

    questions = get_program_questions(program_id)
    tags = get_program_tags(program_id)
    prog_tests = get_program_tests(program_id)

    # Check if user can edit
    logged_in_uid = st.session_state.get("user_id")
    is_owner = prog.get("owner_id") == logged_in_uid
    prog_role = get_user_role_for_program(program_id, logged_in_uid) if logged_in_uid and not is_owner else None
    can_edit_program = _is_global_admin() or is_owner or prog_role in ("reviewer", "admin")

    # Get performance data for all tests in the program
    logged_in = _is_logged_in()
    test_ids = [pt["id"] for pt in prog_tests]
    test_performance = get_tests_performance(logged_in_uid, test_ids) if logged_in else {}

    # Helper to get performance circle
    def _get_perf_circle(pct):
        if pct >= 95:
            return "\U0001f7e2"  # Green: excellent
        elif pct >= 80:
            return "\U0001f7e1"  # Yellow: good
        elif pct >= 50:
            return "\U0001f7e0"  # Orange: needs work
        else:
            return "\U0001f534"  # Red: struggling

    st.header(prog["title"])
    if prog.get("description"):
        st.write(prog["description"])

    st.caption(t("n_tests_n_questions", nt=len(prog_tests), nq=len(questions)))

    # Tests included section with performance and action buttons
    st.subheader(t("tests_included"))
    visible_tests_count = 0
    for pt in prog_tests:
        test_id = pt["id"]
        program_visibility = pt.get("program_visibility", "public")

        # Check if user can see this test based on effective visibility
        test_data = get_test(test_id)
        test_owner_id = test_data.get("owner_id") if test_data else None
        test_base_visibility = test_data.get("visibility", "public") if test_data else "public"

        # Compute effective visibility (the more restrictive of test base and program visibility)
        effective_visibility = get_effective_visibility(test_base_visibility, program_visibility)

        # Check direct access (for private/hidden visibility)
        has_direct_access = (
            _is_global_admin()
            or test_owner_id == logged_in_uid
            or has_direct_test_access(test_id, logged_in_uid)
        )

        # For hidden tests, skip unless user has direct access
        if effective_visibility == "hidden" and not has_direct_access:
            continue  # Skip this hidden test

        visible_tests_count += 1
        perf = test_performance.get(test_id, {})
        pct = perf.get("percent_correct", 0)
        total_answered = perf.get("total", 0)

        # Check edit permission for this specific test
        test_role = get_user_role_for_test(test_id, logged_in_uid) if logged_in_uid else None
        can_edit_test = _is_global_admin() or test_owner_id == logged_in_uid or test_role in ("reviewer", "admin")

        # Determine if user can access this test based on effective visibility
        can_access_test = (
            effective_visibility in ("public", "restricted")
            or has_direct_access
        )

        with st.container(border=True):
            # Title row with performance circle and buttons
            col_info, col_btns = st.columns([3, 2])

            with col_info:
                # Title with performance circle and visibility indicator
                if logged_in and total_answered > 0:
                    circle = _get_perf_circle(pct)
                    title_display = f"{circle} **{pt['title']}**"
                elif logged_in:
                    title_display = f"\u26aa **{pt['title']}**"
                else:
                    title_display = f"**{pt['title']}**"

                # Add visibility indicator for restricted/private tests
                if effective_visibility == "private" and not has_direct_access:
                    title_display += " \U0001f512"
                elif effective_visibility == "restricted":
                    title_display += " \U0001f513"

                st.markdown(title_display)
                st.caption(t("n_questions", n=pt["question_count"]))

            with col_btns:
                # Action buttons: View, Edit (if can_edit), Export (if can_edit)
                num_btns = 3 if can_edit_test else 1
                btn_cols = st.columns(num_btns)
                btn_idx = 0

                with btn_cols[btn_idx]:
                    # Disable button for private tests without access
                    btn_disabled = not can_access_test
                    btn_help = t("select") if can_access_test else t("test_private_no_access")
                    if st.button("\u25b6\ufe0f", key=f"prog_view_test_{test_id}", width="stretch", help=btn_help, disabled=btn_disabled):
                        st.session_state.selected_test = test_id
                        st.session_state.test_program_visibility = program_visibility  # For material visibility
                        st.session_state.page = "Configurar Test"
                        st.rerun()

                if can_edit_test:
                    btn_idx += 1
                    with btn_cols[btn_idx]:
                        if st.button("\u270f\ufe0f", key=f"prog_edit_test_{test_id}", width="stretch", help=t("edit_test")):
                            st.session_state.editing_test_id = test_id
                            st.rerun()
                    btn_idx += 1
                    with btn_cols[btn_idx]:
                        export_data, export_title = _get_test_export_data(test_id)
                        st.download_button(
                            "\u2b07\ufe0f",
                            data=export_data,
                            file_name=f"{export_title}.json",
                            mime="application/json",
                            key=f"prog_export_test_{test_id}",
                            width="stretch",
                            help=t("export_json"),
                        )

            # Expandable performance details
            if logged_in and total_answered > 0:
                with st.expander(t("your_progress")):
                    m1, m2, m3 = st.columns(3)
                    m1.metric(t("total_answered"), total_answered)
                    m2.metric(t("correct_answers"), perf.get("correct", 0))
                    m3.metric(t("percent_correct"), f"{pct}%")

                    # Get topic stats for this test
                    topic_stats = get_topic_statistics(logged_in_uid, test_id)
                    if topic_stats and len(topic_stats) > 1:
                        sorted_topics = sorted(topic_stats.items(), key=lambda x: x[1]["percent_correct"], reverse=True)
                        best_tag, best_stats = sorted_topics[0]
                        worst_tag, worst_stats = sorted_topics[-1]
                        best_display = best_tag.replace("_", " ").title()
                        worst_display = worst_tag.replace("_", " ").title()
                        col_best, col_worst = st.columns(2)
                        col_best.metric(t("best_topic"), best_display, f"{best_stats['percent_correct']}%")
                        col_worst.metric(t("worst_topic"), worst_display, f"{worst_stats['percent_correct']}%")

    if visible_tests_count == 0:
        st.info(t("no_tests_in_course"))

    # Action buttons row
    btn_cols = st.columns(4 if can_edit_program else 2)
    col_idx = 0
    with btn_cols[col_idx]:
        if st.button(t("back_to_courses"), width="stretch"):
            if "selected_program" in st.session_state:
                del st.session_state.selected_program
            st.session_state.page = "Cursos"
            st.rerun()
    col_idx += 1
    if can_edit_program:
        with btn_cols[col_idx]:
            if st.button(f"\u270f\ufe0f {t('edit_program')}", width="stretch"):
                st.session_state.editing_program_id = program_id
                st.session_state.page = "Editar Curso"
                st.rerun()
        col_idx += 1
        with btn_cols[col_idx]:
            export_data, export_title = _get_program_export_data(program_id)
            st.download_button(
                t("export_json"),
                data=export_data,
                file_name=f"{export_title}.json",
                mime="application/json",
                key="export_program_json",
                width="stretch",
            )

    if not questions:
        st.warning(t("no_course_questions"))
        return

    st.subheader(t("configuration"))

    num_questions = st.number_input(
        t("num_questions"),
        min_value=1,
        max_value=len(questions),
        value=min(25, len(questions)),
    )

    st.write(t("topics_to_include"))
    selected_tags = []
    cols = st.columns(2)
    for i, tag in enumerate(tags):
        tag_display = tag.replace("_", " ").title()
        if cols[i % 2].checkbox(tag_display, value=True, key=f"prog_tag_{tag}"):
            selected_tags.append(tag)

    if not selected_tags:
        st.warning(t("select_at_least_one_topic"))
    else:
        filtered_count = len([q for q in questions if q["tag"] in selected_tags])
        st.info(t("available_questions_with_topics", n=filtered_count))

        if st.button(t("start_test"), type="primary"):
            stats = get_question_stats(st.session_state.user_id, program_id) if _is_logged_in() else None
            quiz_questions = select_balanced_questions(
                questions, selected_tags, num_questions, stats
            )
            session_id = None
            if _is_logged_in():
                session_id = create_session(
                    st.session_state.user_id, None,
                    0, len(quiz_questions),
                )
            st.session_state.questions = shuffle_question_options(quiz_questions)
            st.session_state.current_index = 0
            st.session_state.score = 0
            st.session_state.answered = False
            st.session_state.show_result = False
            st.session_state.selected_answer = None
            st.session_state.wrong_questions = []
            st.session_state.round_history = []
            st.session_state.current_round = 1
            st.session_state.current_test_id = 0
            st.session_state.current_session_id = session_id
            st.session_state.quiz_started = True
            st.session_state.page = "Cursos"
            st.rerun()
