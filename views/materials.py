import json
import streamlit as st
from translations import t
from helpers import _fetch_youtube_transcript, LANGUAGE_KEYS, _lang_display
from auth import _is_knowter_or_admin, _is_global_admin
from db import (
    get_all_materials, get_material, create_material, update_material, delete_material,
    save_material_llm_data, get_material_collaborators,
    add_material_collaborator, remove_material_collaborator,
)

MATERIAL_TYPES = ["youtube_video", "pdf", "url", "image", "document"]

MATERIAL_ICONS = {
    "youtube_video": "▶️",
    "pdf": "📄",
    "url": "🔗",
    "image": "🖼️",
    "document": "📝",
}

VISIBILITY_ICONS = {
    "public": "🌐",
    "restricted": "🔒",
    "hidden": "👁",
    "private": "🔐",
}

VISIBILITIES = ["public", "restricted", "hidden", "private"]
COLLAB_ROLES = ["student", "guest", "reviewer", "admin"]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def show_materials():
    st.header(f"📦 {t('materials_title')}")
    st.write(t("materials_desc"))

    page = st.session_state.get("materials_page", "catalog")
    if page == "editor":
        _show_material_editor(st.session_state.get("materials_editing_id"))
    else:
        _show_material_catalog()


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def _show_material_catalog():
    user_id = st.session_state.get("user_id")
    can_create = _is_knowter_or_admin()

    col_search, col_add = st.columns([5, 1])
    with col_search:
        search = st.text_input(t("materials_search"), key="materials_search", label_visibility="collapsed",
                               placeholder=t("materials_search"))
    with col_add:
        if can_create:
            if st.button(t("materials_add"), type="primary", key="materials_add_btn"):
                st.session_state.materials_page = "editor"
                st.session_state.materials_editing_id = None
                st.rerun()

    col_type, col_lang = st.columns(2)
    with col_type:
        type_filter = st.multiselect(
            t("materials_filter_type"),
            options=MATERIAL_TYPES,
            format_func=lambda x: t(f"material_type_{x}"),
            key="materials_type_filter",
        )
    with col_lang:
        lang_opts = [""] + [k for k in LANGUAGE_KEYS if k]
        lang_filter = st.selectbox(
            t("materials_filter_lang"),
            options=lang_opts,
            format_func=lambda x: _lang_display(x) if x else t("materials_all_langs"),
            key="materials_lang_filter",
        )

    materials = get_all_materials(user_id)

    if search:
        q = search.lower()
        materials = [m for m in materials if q in m.get("title", "").lower() or q in m.get("description", "").lower()]
    if type_filter:
        materials = [m for m in materials if m.get("material_type") in type_filter]
    if lang_filter:
        materials = [m for m in materials if m.get("language") == lang_filter]

    if not materials:
        st.info(t("materials_empty"))
        return

    st.divider()
    for mat in materials:
        _render_material_card(mat, user_id)


def _render_material_card(mat, user_id):
    is_owner = mat.get("owner_id") == user_id
    can_edit = _is_global_admin() or is_owner

    icon = MATERIAL_ICONS.get(mat.get("material_type", ""), "📎")
    vis_icon = VISIBILITY_ICONS.get(mat.get("visibility", "public"), "🌐")
    lang = _lang_display(mat.get("language", "")) or ""
    title = mat.get("title") or t("no_title")

    with st.container(border=True):
        col_info, col_actions = st.columns([5, 2])
        with col_info:
            st.markdown(f"**{icon} {title}** {vis_icon}")
            if mat.get("description"):
                st.caption(mat["description"])
            meta = []
            if lang:
                meta.append(lang)
            if mat.get("material_type"):
                meta.append(t(f"material_type_{mat['material_type']}"))
            if mat.get("graph_json"):
                meta.append("📊 " + t("material_has_graph"))
            if meta:
                st.caption(" · ".join(meta))
        with col_actions:
            if can_edit:
                if st.button(t("material_edit"), key=f"mat_edit_{mat['id']}", use_container_width=True):
                    st.session_state.materials_page = "editor"
                    st.session_state.materials_editing_id = mat["id"]
                    st.rerun()
                confirm_key = f"mat_confirm_delete_{mat['id']}"
                if not st.session_state.get(confirm_key):
                    if st.button(t("material_delete"), key=f"mat_del_{mat['id']}", type="secondary", use_container_width=True):
                        st.session_state[confirm_key] = True
                        st.rerun()
                else:
                    st.warning(t("material_delete_confirm"))
                    cy, cn = st.columns(2)
                    with cy:
                        if st.button("✓", key=f"mat_del_yes_{mat['id']}", type="primary"):
                            delete_material(mat["id"])
                            st.session_state.pop(confirm_key, None)
                            st.rerun()
                    with cn:
                        if st.button("✗", key=f"mat_del_no_{mat['id']}"):
                            st.session_state.pop(confirm_key, None)
                            st.rerun()


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------

def _show_material_editor(material_id):
    is_new = material_id is None
    mat = None if is_new else get_material(material_id)

    if st.button(f"← {t('back')}", key="mat_back_btn"):
        st.session_state.materials_page = "catalog"
        st.session_state.pop("materials_editing_id", None)
        st.rerun()

    st.subheader(t("material_new") if is_new else t("material_edit_title"))

    # --- Basic info ---
    with st.form("material_basic_form"):
        title = st.text_input(t("material_title_field"), value=mat.get("title", "") if mat else "")
        description = st.text_area(t("material_desc_field"), value=mat.get("description", "") if mat else "", height=80)

        col1, col2 = st.columns(2)
        with col1:
            cur_type = mat.get("material_type", MATERIAL_TYPES[0]) if mat else MATERIAL_TYPES[0]
            mat_type = st.selectbox(
                t("material_type_field"),
                options=MATERIAL_TYPES,
                format_func=lambda x: f"{MATERIAL_ICONS.get(x, '📎')} {t(f'material_type_{x}')}",
                index=MATERIAL_TYPES.index(cur_type) if cur_type in MATERIAL_TYPES else 0,
            )
        with col2:
            cur_vis = mat.get("visibility", "public") if mat else "public"
            visibility = st.selectbox(
                t("material_visibility_field"),
                options=VISIBILITIES,
                format_func=lambda x: f"{VISIBILITY_ICONS[x]} {t(f'visibility_{x}')}",
                index=VISIBILITIES.index(cur_vis) if cur_vis in VISIBILITIES else 0,
            )

        col3, col4 = st.columns(2)
        with col3:
            lang_opts = [""] + [k for k in LANGUAGE_KEYS if k]
            cur_lang = mat.get("language", "") if mat else ""
            language = st.selectbox(
                t("material_lang_field"),
                options=lang_opts,
                format_func=lambda x: _lang_display(x) if x else "—",
                index=lang_opts.index(cur_lang) if cur_lang in lang_opts else 0,
            )
        with col4:
            url = st.text_input(t("material_url_field"), value=mat.get("url", "") if mat else "")

        saved = st.form_submit_button(t("material_save"), type="primary")

    if saved:
        if not title.strip():
            st.error(t("material_title_required"))
        else:
            user_id = st.session_state.get("user_id")
            if is_new:
                new_id = create_material(user_id, title.strip(), description, mat_type, language, visibility, url.strip())
                if new_id:
                    st.session_state.materials_editing_id = new_id
                    material_id = new_id
                    mat = get_material(new_id)
                    st.success(t("material_saved"))
                    st.rerun()
            else:
                update_material(material_id, title=title.strip(), description=description,
                                material_type=mat_type, language=language, visibility=visibility, url=url.strip())
                st.success(t("material_saved"))
                mat = get_material(material_id)
                st.rerun()

    # --- Sections below basic info (only when material exists) ---
    mid = material_id or st.session_state.get("materials_editing_id")
    if mid:
        if mat is None:
            mat = get_material(mid)
        if mat:
            st.divider()
            _show_material_llm_section(mat)
            st.divider()
            _show_material_collaborators(mid)
            st.divider()
            _show_material_actions(mat)


# ---------------------------------------------------------------------------
# LLM analysis section
# ---------------------------------------------------------------------------

def _show_material_llm_section(mat):
    from views.research import (
        _build_concept_graph_prompt_seq, _add_facts, _generate_questions,
        _display_concept_graph, _display_grounded_graph, _display_questions,
        _ground_graph,
    )

    st.subheader(t("material_llm_title"))
    st.caption(t("material_llm_desc"))

    default_or_key = st.secrets.get("OPENROUTE_API_KEY", "")
    default_hf_key = st.secrets.get("HF_API_KEY", "")
    default_gemini_key = st.secrets.get("GEMIMI_API_KEY", "")

    provider = st.radio(
        t("research_provider"),
        options=["openrouter", "huggingface", "gemini"],
        format_func=lambda x: t(f"research_provider_{x}"),
        horizontal=True,
        key="mat_provider",
    )

    col_key, col_model = st.columns(2)
    with col_key:
        if provider == "openrouter":
            api_key = st.text_input(t("research_or_key"), value=default_or_key, type="password", key="mat_or_key")
            default_model = st.secrets.get("OR_MODEL", "qwen/qwen-2.5-72b-instruct")
        elif provider == "gemini":
            api_key = st.text_input(t("research_gemini_key"), value=default_gemini_key, type="password", key="mat_gemini_key")
            default_model = st.secrets.get("GEMINI_MODEL", "gemini-2.0-flash")
        else:
            api_key = st.text_input(t("research_hf_token"), value=default_hf_key, type="password", key="mat_hf_key")
            default_model = st.secrets.get("HF_MODEL", "Qwen/Qwen2.5-72B-Instruct")
    with col_model:
        model = st.text_input(t("research_hf_model"), value=default_model, key="mat_model")

    api_key = api_key or (
        default_or_key if provider == "openrouter"
        else default_gemini_key if provider == "gemini"
        else default_hf_key
    )

    graph_data = mat.get("graph_json")
    facts_data = mat.get("facts_json")
    questions_data = mat.get("questions_json") or []
    segments_data = mat.get("segments_json")
    transcript = mat.get("transcript", "")
    is_youtube = mat.get("material_type") == "youtube_video"

    if is_youtube and mat.get("url"):
        from views.research import _compute_segments
        num_questions = st.number_input(
            t("material_num_questions"), min_value=1, max_value=100, value=10, step=1,
            key="mat_num_questions"
        )

        if not graph_data:
            # First-time analysis: fetch transcript + build everything
            if st.button(t("material_analyse_btn"), type="primary", key="mat_analyse_btn", disabled=not api_key):
                with st.spinner(t("research_fetching_transcript")):
                    transcript = _fetch_youtube_transcript(mat["url"])
                if not transcript:
                    st.error(t("research_no_transcript"))
                else:
                    save_material_llm_data(mat["id"], transcript=transcript)
                    subject = f"{mat.get('title', '')} {mat.get('description', '')}".strip()

                    with st.spinner(t("research_building_graph")):
                        graph_data = _build_concept_graph_prompt_seq(
                            transcript, api_key, model, subject_matter=subject, provider=provider
                        )

                    if graph_data:
                        save_material_llm_data(mat["id"], graph_json=graph_data)

                        with st.spinner(t("research_enriching_graph")):
                            grounded = _ground_graph(graph_data, transcript)

                        if grounded:
                            with st.spinner(t("research_extracting_facts")):
                                facts_data = _add_facts(grounded, api_key, model, provider=provider)
                            if facts_data:
                                save_material_llm_data(mat["id"], facts_json=facts_data)

                        with st.spinner(t("research_generating_questions")):
                            questions_data = _generate_questions(
                                graph_data, transcript, num_questions, api_key, model, provider=provider
                            )
                        if questions_data:
                            save_material_llm_data(mat["id"], questions_json=questions_data)

                        segments_data = _compute_segments(transcript, graph_data, api_key, model, provider)
                        if segments_data:
                            save_material_llm_data(mat["id"], segments_json=segments_data)

                        st.success(t("material_analysis_done"))
                        st.rerun()
        else:
            # Graph already exists — show regeneration buttons
            if not transcript:
                st.info(t("material_no_transcript"))
            else:
                # Row 1: graph and facts
                col_graph, col_facts = st.columns(2)
                with col_graph:
                    if st.button(t("material_regen_graph_btn"), key="mat_regen_graph_btn", disabled=not api_key, use_container_width=True):
                        subject = f"{mat.get('title', '')} {mat.get('description', '')}".strip()
                        with st.spinner(t("research_building_graph")):
                            new_graph = _build_concept_graph_prompt_seq(
                                transcript, api_key, model, subject_matter=subject, provider=provider
                            )
                        if new_graph:
                            save_material_llm_data(mat["id"], graph_json=new_graph)
                            graph_data = new_graph
                            st.success(t("material_graph_done"))
                            st.rerun()
                with col_facts:
                    if st.button(t("material_regen_facts_btn"), key="mat_regen_facts_btn", disabled=not api_key, use_container_width=True):
                        with st.spinner(t("research_enriching_graph")):
                            grounded = _ground_graph(graph_data, transcript)
                        if grounded:
                            with st.spinner(t("research_extracting_facts")):
                                new_facts = _add_facts(grounded, api_key, model, provider=provider)
                            if new_facts:
                                save_material_llm_data(mat["id"], facts_json=new_facts)
                                st.success(t("material_facts_done"))
                                st.rerun()

                # Row 2: questions and segments
                col_regen, col_add, col_seg = st.columns(3)
                with col_regen:
                    if st.button(t("material_regen_questions_btn"), key="mat_regen_q_btn", disabled=not api_key, use_container_width=True):
                        with st.spinner(t("research_generating_questions")):
                            new_qs = _generate_questions(
                                graph_data, transcript, num_questions, api_key, model, provider=provider
                            )
                        if new_qs:
                            save_material_llm_data(mat["id"], questions_json=new_qs)
                            st.success(t("material_questions_done"))
                            st.rerun()
                with col_add:
                    if st.button(t("material_add_questions_btn"), key="mat_add_q_btn", disabled=not api_key, use_container_width=True):
                        with st.spinner(t("research_generating_questions")):
                            new_qs = _generate_questions(
                                graph_data, transcript, num_questions, api_key, model, provider=provider
                            )
                        if new_qs:
                            combined = questions_data + new_qs
                            save_material_llm_data(mat["id"], questions_json=combined)
                            st.success(t("material_questions_done"))
                            st.rerun()
                with col_seg:
                    if st.button(t("material_regen_segments_btn"), key="mat_regen_seg_btn", disabled=not api_key, use_container_width=True):
                        new_segs = _compute_segments(transcript, graph_data, api_key, model, provider)
                        if new_segs:
                            save_material_llm_data(mat["id"], segments_json=new_segs)
                            st.success(t("material_segments_done"))
                            st.rerun()
    elif not is_youtube:
        st.info(t("material_llm_youtube_only"))

    if graph_data:
        with st.expander(t("material_graph_title"), expanded=False):
            _display_concept_graph(graph_data)
    if facts_data:
        with st.expander(t("material_facts_title"), expanded=False):
            _display_grounded_graph(facts_data)
    if questions_data:
        with st.expander(t("material_questions_title"), expanded=False):
            _display_questions(questions_data, facts=facts_data)
    if segments_data:
        from views.research import _display_segments
        with st.expander(t("material_segments_title"), expanded=False):
            _display_segments(segments_data, grounded_data=facts_data, questions=questions_data,
                              key_prefix=f"mat_seg_{mat['id']}")


# ---------------------------------------------------------------------------
# Collaborators
# ---------------------------------------------------------------------------

def _show_material_collaborators(material_id):
    st.subheader(t("material_collaborators"))
    collabs = get_material_collaborators(material_id)

    if collabs:
        for c in collabs:
            col_email, col_role, col_status, col_rm = st.columns([3, 2, 2, 1])
            with col_email:
                st.write(c["user_email"])
            with col_role:
                st.write(c["role"])
            with col_status:
                st.write(c.get("status", "pending"))
            with col_rm:
                if st.button("✕", key=f"mat_rm_{material_id}_{c['user_email']}"):
                    remove_material_collaborator(material_id, c["user_email"])
                    st.rerun()
    else:
        st.caption(t("material_no_collaborators"))

    with st.form(f"mat_invite_{material_id}"):
        col_e, col_r, col_b = st.columns([3, 2, 1])
        with col_e:
            email = st.text_input(t("material_invite_email"))
        with col_r:
            role = st.selectbox(t("material_invite_role"), options=COLLAB_ROLES)
        with col_b:
            st.write("")
            invite = st.form_submit_button(t("material_invite_btn"))
        if invite and email.strip():
            add_material_collaborator(material_id, email.strip(), role)
            st.success(t("material_invited"))
            st.rerun()


# ---------------------------------------------------------------------------
# Export & delete actions
# ---------------------------------------------------------------------------

def _show_material_actions(mat):
    col_exp, col_del = st.columns(2)
    with col_exp:
        export = {
            "title": mat.get("title", ""),
            "description": mat.get("description", ""),
            "material_type": mat.get("material_type", ""),
            "language": mat.get("language", ""),
            "visibility": mat.get("visibility", ""),
            "url": mat.get("url", ""),
            "transcript": mat.get("transcript", ""),
            "graph": mat.get("graph_json"),
            "facts": mat.get("facts_json"),
            "questions": mat.get("questions_json"),
        }
        st.download_button(
            t("material_export"),
            data=json.dumps(export, ensure_ascii=False, indent=2),
            file_name=f"material_{mat['id']}.json",
            mime="application/json",
            key=f"mat_export_{mat['id']}",
        )
    with col_del:
        confirm_key = "mat_editor_confirm_delete"
        if not st.session_state.get(confirm_key):
            if st.button(t("material_delete"), type="secondary", key=f"mat_del_editor_{mat['id']}"):
                st.session_state[confirm_key] = True
                st.rerun()
        else:
            st.warning(t("material_delete_confirm"))
            cy, cn = st.columns(2)
            with cy:
                if st.button("✓", key="mat_del_editor_yes", type="primary"):
                    delete_material(mat["id"])
                    st.session_state.materials_page = "catalog"
                    st.session_state.pop("materials_editing_id", None)
                    st.session_state.pop(confirm_key, None)
                    st.rerun()
            with cn:
                if st.button("✗", key="mat_del_editor_no"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()
