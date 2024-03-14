
# A very simple Flask Hello World app for you to get started with...

from flask import Flask, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['DEBUG'] = True

SQLALCHEMY_DATABASE_URI = (
    'mysql+mysqlconnector://{username}:{password}@{hostname}/{databasename}'
    .format(
        username='gregc',
        password='0Vw6qWVanF6kcus',
        hostname='gregc.mysql.pythonanywhere-services.com',
        databasename='gregc$comments',
    )
)

app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_POOL_RECYCLE'] = 299
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class Comment(db.Model):

    __tablename__ = 'comments'
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(4096))

comments = []

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'GET':
        return render_template('main_page.html', comments=comments)

    comments.append(request.form['contents'])

    # refresh main page (to add the new comment to the view)
    return redirect(url_for('index'))

