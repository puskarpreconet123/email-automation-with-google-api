from flask import Flask, render_template, request, redirect
import os
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import json

from dotenv import load_dotenv
load_dotenv()

# steps  for sendin via google api . protocol - http, step-1

import base64
from email.message import EmailMessage
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
 
 #---------------------------------------------------

app = Flask(__name__)

client = MongoClient(os.getenv("MONGO_URI"))
db = client["emailAutomation"]
users_collection = db["users"]


# steps  for sendin via google api . protocol - http, step-2
 
def send_email_api(to_email, subject, template, data):
    try:
        # Load the token.json from env variable
        token_str = os.getenv("GMAIL_TOKEN_JSON")
        if not token_str:
            print("Error in token_str: GMAIL_TOKEN_JSON environment variable is not set!")
            return False
        tokeninfo=json.loads(token_str)
        creds = Credentials.from_authorized_user_info(tokeninfo)
        service = build('gmail', 'v1', credentials=creds)

        # Render your HTML template
        html_body = render_template(template, **data)

        # Build the message
        message = EmailMessage()
        message.set_content("HTML email content.")
        message.add_alternative(html_body, subtype='html')
        message['To'] = to_email
        message['From'] = "me" # Google automatically uses your email
        message['Subject'] = subject

        # Encode for API
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        # Send
        service.users().messages().send(userId="me", body={'raw': raw_message}).execute()
        return True
    except Exception as e:
        print(f"Error in send_email_api function: {e}")
        return False
#-------------------------------------------------------------------------------------

def send_email(user):
    try:
        users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$inc": {"noOfEmailsSend": 1},
            "$set": {"lastSend": datetime.now()}
        }
        )

        send_email_api(
            user["email"],
            user["subject"],
            "email.html",
            {
                "name": user["name"],
                "message": user["message"],
                "sender": "Backend Team"
            }
        )
        return True
    except Exception as e:
        print(f"Error in send_email function: {e}")
        return False

@app.route("/emailForm")
def emailForm():
  return  render_template("emailAutomationForm.html", active="emailForm")

@app.route("/", methods=["GET", "POST"])
def dashboard():
    if request.method == "POST":
        name = request.form["name"]
        toEmail = request.form["email"]
        subject = request.form["subject"]
        message = request.form["message"]
        group = request.form["group"]

        user_exist = users_collection.find_one({"email": toEmail}) is not None
        if user_exist:
            return render_template("showerror.html", error_message="Email already exists")

        users_collection.insert_one({
            "name": name,
            "email": toEmail,
            "subject": subject,
            "message": message,
            "group": group,
            "noOfEmailsSend": 0,
            "dateCreated": datetime.now(),
            "lastSend": None
        })

        return redirect("/")

    all_users = list(users_collection.find())

    total_users = users_collection.count_documents({})
    total_emails_send = sum(u.get("noOfEmailsSend", 0) for u in all_users)

    return render_template(
        "dashboard.html",
        allUser=all_users,
        active="home",
        users=total_users,
        emails=total_emails_send
    )

@app.route('/details/<id>')
def user_details(id):
    user = users_collection.find_one({"_id": ObjectId(id)})
    return render_template("userCard.html", user=user)


@app.route('/delete/<id>')
def delete_user(id):
    users_collection.delete_one({"_id": ObjectId(id)})
    return redirect("/")


@app.route('/emailForm-edit/<id>', methods=['GET', 'POST'])
def update_user(id):
    if request.method == "POST":
        users_collection.update_one(
            {"_id": ObjectId(id)},
            {"$set": {
                "name": request.form["name"],
                "email": request.form["email"],
                "subject": request.form["subject"],
                "message": request.form["message"],
                "group": request.form["group"]
            }}
        )
        return redirect("/")

    user = users_collection.find_one({"_id": ObjectId(id)})
    return render_template("emailAutomationForm.html", user=user)


@app.route("/start")
def start_scheduler():
    global scheduler
    if scheduler and scheduler.running:
        return render_template('showerror.html', error_message="Scheduler already running")

    create_scheduler()
    return redirect("/")


@app.route("/stop")
def stop_scheduler():
    global scheduler
    if not scheduler or not scheduler.running:
        return render_template('showerror.html', error_message="Scheduler already stopped")

    scheduler.shutdown()
    scheduler = None
    return redirect("/")
 

@app.route("/status")
def scheduler_status():
    return {"running": scheduler.running if scheduler else False}



def job():
    with app.app_context():
        all_users = list(users_collection.find())
        now = datetime.now()

        for user in all_users:
            if user["lastSend"] is None:
                send_email(user)
                continue

            diff = now - user["lastSend"]

            if user["group"].lower() == "student":
                if user["noOfEmailsSend"] < 3 and diff >= timedelta(minutes=1):
                    send_email(user)
                elif user["noOfEmailsSend"] >= 3 and diff >= timedelta(minutes=5):
                    send_email(user)

            elif user["group"].lower() == "teacher":
                if user["noOfEmailsSend"] <=5:
                    send_email(user)


scheduler = None

def create_scheduler():
    global scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(job, 'interval', seconds=20, id="email_scheduler")
    scheduler.start()

# scheduler.add_job(job, 'interval', seconds=20, id="email_scheduler")

if __name__ == "__main__":
    create_scheduler()
    app.run()