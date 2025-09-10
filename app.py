import os
from datetime import datetime, timedelta

from flask import Flask, request, jsonify
from flask_login import LoginManager
from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy.orm import joinedload

from models import db, WastePackage, StatusEvent, Hospital
from auth import bp as auth_bp, login_manager
from upload_csv import bp as upload_bp
from views import bp as views_bp

# โหลด environment ก่อน
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

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    @app.route("/api/ask-gpt", methods=["POST"])
    def ask_gpt():
        data = request.get_json()
        user_question = data.get("question")

        if not user_question:
            return jsonify({"error": "No question provided"}), 400

        try:
            # --- Data Collection for GPT Prompt ---
            now = datetime.utcnow()
            dt_from = now - timedelta(days=30)

            wastes = WastePackage.query.filter(
                (WastePackage.collected_time == None)
                | (WastePackage.collected_time >= dt_from)
            ).all()

            total_wastes = len(wastes)
            by_type = {}
            for w in wastes:
                by_type[w.waste_type] = by_type.get(w.waste_type, 0) + 1

            waste_summary = []
            for w in wastes[:10]:  # limit เหลือ 10 เพื่อลด token
                hospital = Hospital.query.get(w.hospital_id)
                current_status_event = (
                    StatusEvent.query.filter_by(ref_type="waste", ref_id=w.waste_id)
                    .order_by(StatusEvent.at.desc())
                    .first()
                )
                current_status = (
                    current_status_event.status if current_status_event else "Unknown"
                )
                waste_summary.append(
                    f"ID:{w.waste_id}, Type:{w.waste_type}, "
                    f"Weight:{w.weight_kg}kg, "
                    f"Hospital:{hospital.name if hospital else 'N/A'}, "
                    f"Status:{current_status}"
                )

            incidents_exist = (
                StatusEvent.query.filter(StatusEvent.status.like("%incident%")).count()
                > 0
            )

            # --- Build messages for GPT ---
            system_msg = (
                "คุณคือผู้ช่วยวิเคราะห์การจัดการขยะการแพทย์ "
                "ตอบเป็นภาษาไทย มืออาชีพ กระชับ ให้ข้อมูลครบ: "
                "1) ความเสี่ยง/ความผิดปกติ 2) แนวโน้ม 3) คำแนะนำปฏิบัติ "
                "อธิบายเหตุผลสั้นๆ ก่อนสรุป ตอบไม่เกิน 150 คำ."
            )

            user_msg = "\n".join(
                [
                    f"ช่วงเวลา: 30 วันล่าสุด",
                    f"รวมแพ็กเกจ: {total_wastes}",
                    f"ประเภท: {by_type}",
                    "ตัวอย่างแพ็กเกจ (10 แรก):",
                    "\n".join(waste_summary) if waste_summary else "ไม่มีข้อมูล",
                    f"มี incident หรือไม่: {'Yes' if incidents_exist else 'No'}",
                    f"คำถาม: {user_question}",
                ]
            )

            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.2,
                max_tokens=500,
            )
            gpt_response = completion.choices[0].message.content.strip()
            return jsonify({"response": gpt_response})

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.template_filter("fmt_dt")
    def fmt_dt(dt):
        return dt.strftime("%Y-%m-%d %H:%M") if dt else "-"

    return app

if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        db.create_all()
    app.run(debug=True)
