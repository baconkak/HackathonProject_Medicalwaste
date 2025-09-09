import os
from flask import Flask
from flask_login import LoginManager
from dotenv import load_dotenv
from models import db
from auth import bp as auth_bp, login_manager
from upload_csv import bp as upload_bp
from views import bp as views_bp

load_dotenv()

def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", "sqlite:///medwaste.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["DEFAULT_BUFFER_METERS"] = int(os.getenv("DEFAULT_BUFFER_METERS", "150"))

    db.init_app(app)
    login_manager.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(views_bp)

    @app.template_filter("fmt_dt")
    def fmt_dt(dt):
        return dt.strftime("%Y-%m-%d %H:%M") if dt else "-"

    return app

if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        db.create_all()
    app.run(debug=True)
