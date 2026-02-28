import streamlit as st
import base64
from translations import t
from db import (
    user_exists, get_or_create_google_user, resolve_collaborator_user_id,
    get_user_profile, get_user_global_role,
    get_user_survey_status, get_active_periodic_survey, get_active_initial_survey,
    has_completed_survey, put_access_on_hold,
)


def _is_logged_in():
    """Return True if user is authenticated."""
    return bool(st.session_state.get("user_id"))


def _try_login():
    """Attempt to log in the user silently, supporting both st.user and st.experimental_user.

    For new users (not in database), stores pending registration info and requires
    terms acceptance before creating the account.
    """
    if st.session_state.get("user_id"):
        return

    user_info = getattr(st, "user", getattr(st, "experimental_user", None))

    if user_info and hasattr(user_info, "is_logged_in"):
        try:
            if user_info.is_logged_in:
                email = user_info.email
                name = getattr(user_info, "name", email) or email

                # Check if user already exists
                if user_exists(email):
                    # Existing user - log in directly
                    user_id = get_or_create_google_user(email, name)
                    resolve_collaborator_user_id(email, user_id)
                    st.session_state.user_id = user_id
                    st.session_state.username = email
                    st.session_state.display_name = name
                else:
                    # New user - store pending registration for terms acceptance
                    st.session_state.pending_registration = {
                        "email": email,
                        "name": name,
                    }
        except Exception as e:
            st.warning(t("auth_not_configured", e=e))


def _get_avatar_html(avatar_bytes, size=35):
    """Return HTML for a circular avatar image, or initials if no avatar."""
    if avatar_bytes:
        b64 = base64.b64encode(avatar_bytes).decode()
        return f'<img src="data:image/png;base64,{b64}" style="width:{size}px;height:{size}px;border-radius:50%;object-fit:cover;">'
    initial = st.session_state.get("display_name", st.session_state.get("username", "?"))[0].upper()
    return (
        f'<div style="width:{size}px;height:{size}px;border-radius:50%;'
        f'background:#4A90D9;color:white;display:flex;align-items:center;'
        f'justify-content:center;font-size:{size//2}px;font-weight:bold;">'
        f'{initial}</div>'
    )


def _load_profile_to_session():
    """Load user profile from DB into session state if not cached."""
    if "profile_loaded" not in st.session_state:
        profile = get_user_profile(st.session_state.user_id)
        st.session_state.display_name = profile["display_name"] or st.session_state.username
        st.session_state.avatar_bytes = profile["avatar"]
        st.session_state.global_role = get_user_global_role(st.session_state.user_id)
        st.session_state.profile_loaded = True


def _get_global_role():
    """Get the current user's global role. Returns 'knower' if not logged in."""
    return st.session_state.get("global_role", "knower")


def _is_knowter_or_admin():
    """Check if user has knowter or admin global role."""
    return _get_global_role() in ("knowter", "admin")


def _is_global_admin():
    """Check if user has admin global role."""
    return _get_global_role() == "admin"


def _is_visitor():
    """Check if user has visitor global role."""
    return _get_global_role() == "visitor"


def _can_create_tests():
    """Check if user can create tests. Visitors and testers cannot create tests."""
    return _get_global_role() in ("knower", "knowter", "admin")


def _can_create_programs():
    """Check if user can create programs. Only knowter and admin users can create programs."""
    return _get_global_role() in ("knowter", "admin")


def _needs_survey():
    """Check if current user needs to complete a survey to maintain access.

    Returns (needs_survey: bool, survey: dict or None)
    """
    if not _is_logged_in():
        return False, None
    user_id = st.session_state.get("user_id")
    status = get_user_survey_status(user_id)
    if not status or status["knowter_access_type"] != "survey":
        return False, None
    if status["access_revoked"]:
        return False, None
    if status["pending_approval"]:
        return False, None

    # Check if periodic survey is needed
    active_survey = get_active_periodic_survey()
    if active_survey and not has_completed_survey(user_id, active_survey["id"]):
        return True, active_survey
    return False, None


def _check_survey_deadline():
    """Check if user's survey deadline has passed and return warning info.

    Returns dict with keys: warning, days_remaining, deadline, overdue
    """
    if not _is_logged_in():
        return None
    user_id = st.session_state.get("user_id")
    status = get_user_survey_status(user_id)
    if not status or status["knowter_access_type"] != "survey":
        return None
    if status["access_revoked"] or status["pending_approval"]:
        return None

    if not status["survey_deadline"]:
        return None

    from datetime import datetime
    try:
        deadline = datetime.fromisoformat(status["survey_deadline"])
        now = datetime.now()
        days_remaining = (deadline - now).days

        return {
            "deadline": deadline.strftime("%Y-%m-%d"),
            "days_remaining": days_remaining,
            "warning": days_remaining <= 7,
            "overdue": days_remaining < 0
        }
    except (ValueError, TypeError):
        return None


def _is_pending_approval():
    """Check if user is awaiting admin approval for role upgrade."""
    if not _is_logged_in():
        return False
    user_id = st.session_state.get("user_id")
    status = get_user_survey_status(user_id)
    if not status:
        return False
    return status.get("pending_approval", False)


def _needs_survey_for_feature():
    """Check if a survey-based user needs to complete a survey before accessing features.
    Returns a tuple: (needs_survey, survey_type, survey)
    - needs_survey: True if user needs to complete a survey
    - survey_type: 'initial' or 'periodic'
    - survey: The survey object to show, or None
    """
    if not _is_logged_in():
        return (False, None, None)

    user_id = st.session_state.get("user_id")
    current_role = _get_global_role()

    # Only check for knower/knowter with survey-based access
    if current_role not in ("knower", "knowter"):
        return (False, None, None)

    status = get_user_survey_status(user_id)
    if not status:
        return (False, None, None)

    # Paid or granted access types don't need surveys
    if status.get("knowter_access_type") in ("paid", "granted"):
        return (False, None, None)

    # Check if initial survey is not completed
    if not status.get("initial_survey_completed"):
        survey = get_active_initial_survey()
        if survey and not has_completed_survey(user_id, survey["id"]):
            return (True, "initial", survey)

    # Check if access is on hold (needs periodic survey)
    if status.get("access_on_hold"):
        survey = get_active_periodic_survey()
        if survey and not has_completed_survey(user_id, survey["id"]):
            return (True, "periodic", survey)

    # Check if deadline passed and needs periodic survey
    from datetime import datetime
    deadline = status.get("survey_deadline")
    if deadline:
        try:
            deadline_dt = datetime.fromisoformat(deadline)
            if datetime.now() > deadline_dt:
                # Deadline passed, put on hold and require survey
                put_access_on_hold(user_id)
                survey = get_active_periodic_survey()
                if survey and not has_completed_survey(user_id, survey["id"]):
                    return (True, "periodic", survey)
        except (ValueError, TypeError):
            pass

    return (False, None, None)


def _show_survey_required_message(survey_type, survey, return_page):
    """Show a message that a survey is required and a button to take it."""
    st.warning(t("access_on_hold_message"))

    if survey:
        if st.button(t("take_survey_now"), type="primary"):
            st.session_state["return_after_survey"] = return_page
            if survey_type == "initial":
                st.session_state.page = "Take Initial Survey"
            else:
                st.session_state.page = "Take Periodic Survey"
                st.session_state["pending_survey"] = survey
            st.rerun()
    else:
        st.info(t("no_active_survey"))
