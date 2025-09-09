from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    UserMixin,
    current_user,
)
from werkzeug.security import check_password_hash
from models import db, User, Role

bp = Blueprint("auth", __name__)

login_manager = LoginManager()
login_manager.login_view = "auth.login"


# Flask-Login adapter
class LoginUser(UserMixin):
    def __init__(self, u: User):
        self.id = u.user_id
        self.username = u.username
        self.role = u.role.name
        self.hospital_id = u.hospital_id
        self.dept_id = u.dept_id
        self.transport_code = u.transport_code


@login_manager.user_loader
def load_user(user_id):
    u = User.query.get(int(user_id))
    return LoginUser(u) if u else None


@bp.route("/auth/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(LoginUser(user))
            flash(f"Logged in as {username} (Role: {user.role.name})", "success")
            return redirect(url_for("views.index"))
        flash("Invalid credentials", "danger")
    return render_template("login.html")


@bp.route("/auth/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


# Role guards
from functools import wraps


def require_roles(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in roles:
                flash("Permission denied", "danger")
                return redirect(url_for("views.index"))
            return fn(*args, **kwargs)

        return wrapper

    return deco
