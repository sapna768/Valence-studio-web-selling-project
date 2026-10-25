from flask import Flask, render_template, request, jsonify

app = Flask(__name__, template_folder='.', static_folder='.', static_url_path='')

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/contact', methods=['POST'])
def contact():
    data = request.json or {}
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    service = data.get('service', '').strip()
    details = data.get('details', '').strip()
    
    if not name or not email:
        return jsonify({"success": False, "message": "Name and Email are required!"}), 400

    return jsonify({"success": True, "message": "Thank you! Your inquiry has been received."})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)