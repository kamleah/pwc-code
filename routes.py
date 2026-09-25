from flask import Flask, render_template, jsonify, send_from_directory, send_file, abort, request, redirect, Response, session, url_for, current_app
from app import app
import os
import io
import mysql.connector
from mysql.connector import Error
from PIL import Image, ImageDraw, ImageFont
import datetime
# from onelogin.saml2.auth import OneLogin_Saml2_Auth
# from onelogin.saml2.utils import OneLogin_Saml2_Utils
from urllib.parse import urlparse
from functools import wraps
from flask_cors import CORS
import traceback

# Function to create MySQL connection
def get_db_connection():
	try:
		# Use environment variables with the appropriate prefix based on mode
		connection = mysql.connector.connect(
			host=os.environ.get('AZURE_{0}_MYSQL_HOST'.format(app.config["ENV_MODE"])),
			port=int(os.environ.get('AZURE_{0}_MYSQL_PORT'.format(app.config["ENV_MODE"]), 3306)),
			user=os.environ.get('AZURE_{0}_MYSQL_USER'.format(app.config["ENV_MODE"])),
			password=os.environ.get('AZURE_{0}_MYSQL_PASSWORD'.format(app.config["ENV_MODE"])),
			database=os.environ.get('AZURE_{0}_MYSQL_DB'.format(app.config["ENV_MODE"])),
			# ssl_ca=os.environ.get('AZURE_{0}_MYSQL_SSL_CA'.format(app.config["ENV_MODE"]), ''),
			# ssl_verify_cert=True if os.environ.get('AZURE_{0}_MYSQL_SSL_VERIFY'.format(app.config["ENV_MODE"]), 'true').lower() == 'true' else False,
			connection_timeout=10
		)
		current_app.logger.info("Connected to MySQL ({0} environment)".format(app.config["ENV_MODE"]))
		return connection
	except Error as e:
		current_app.logger.error("Error connecting to MySQL ({0} environment): {e}".format(app.config["ENV_MODE"]))
		return None

# decorator to check if user is logged in
# This decorator checks if the user is logged in by looking for a session variable.
def login_required(func):
	@wraps(func)
	def func_login_decorator(*args, **kwargs):
		# Store the requested URL for redirecting after authentication
		# session['next_url'] = request.url
		if "user" not in session:
			sso_url = os.environ.get('AZURE_{0}_SSO_URL'.format(app.config["ENV_MODE"]))
			current_app.logger.info(f"SSO URL: {sso_url}")
			if sso_url:
				session.clear()
				return redirect(sso_url)
			else:
				# If SSO URL is not set, redirect to a login page or handle accordingly
				current_app.logger.error("SSO URL not configured in environment variables.")
				return redirect(url_for("forbidden"))
		return func(*args, **kwargs)
	return func_login_decorator

CORS(app, resources={r"/*": {"origins": ["https://hgg.pwc.com"]}})

@app.route('/static/js/app.js')
@login_required
def protected_app_js():
    js_file_path = os.environ.get('AZURE_{0}_JS_DIRECTORY'.format(app.config["ENV_MODE"]))

    if not js_file_path:
        raise Exception("AZURE_DEV_JS_DIRECTORY is not set in the environment.")

    if not os.path.isfile(js_file_path):
        raise FileNotFoundError(f"JavaScript file not found at {js_file_path}")

    return send_file(js_file_path, mimetype='application/javascript')

@app.before_request
def before_request():
	# Check if user is logged in
	user_id = session.get('user')
	if user_id:
		current_time = datetime.datetime.now()  # Use datetime.datetime.now() since you imported datetime module
		last_active = session.get('last_active')
		if last_active:
			# Convert string to datetime if stored as string
			if isinstance(last_active, str):
				last_active = datetime.datetime.fromisoformat(last_active)
			
			# Check if idle for more than 10 minutes
			if (current_time - last_active).total_seconds() > 600:  # 10 minutes in seconds 
				session.clear()
				return redirect(url_for('logout', reason='timeout'))
		
		# Update last active time
		session['last_active'] = current_time.isoformat()

@app.after_request
def set_secure_headers(response):
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data:; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "connect-src 'self'; "
        "frame-ancestors 'self';"
    )
    return response

def load_saml_auth(request):
	# Load SAML settings from the configuration file
	saml_settings_path = os.path.join(app.root_path, 'saml')
	# auth = OneLogin_Saml2_Auth(request, custom_base_path=saml_settings_path)
	return auth

# to serve the favicon
@app.route('/favicon.ico')
def favicon():
	return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

# Custom 404 Error Handler
@app.errorhandler(404)
def not_found_error(error):
	return render_template('404.html'), 404

# Custom 500 Error Handler
@app.errorhandler(500)
def internal_error(error):
	return render_template('500.html'), 500

# main index page
@app.route('/')
@login_required
def index():
	user = session.get('user', None)
	return render_template('generator.html', user=user)

# to get the list of templates
@app.route('/api/templates')
@login_required
def get_templates():
	try:
		connection = get_db_connection()
		if connection is None:
			return jsonify({"error": "Database connection failed"}), 500
		cursor = connection.cursor(dictionary=True)
		cursor.execute("SELECT id, title, image_url FROM tbl_templates ORDER BY title")
		templates = cursor.fetchall()
		cursor.close()
		connection.close()
		return jsonify(templates)
	except Exception as e:
		current_app.logger.error("Error fetching templates: {0}".format(e))
		return jsonify({"error": "Failed to fetch templates"}), 500

# to get the words from the database
@app.route('/api/words')
@login_required
def get_words():
	try:
		connection = get_db_connection()
		if connection is None:
			return jsonify({"error": "Database connection failed"}), 500
		cursor = connection.cursor(dictionary=True)
		cursor.execute("SELECT id, title FROM tbl_words ORDER BY title")
		words = cursor.fetchall()
		cursor.close()
		connection.close()
		return jsonify(words)
	except Exception as e:
		current_app.logger.error(f"Error fetching words: {e}")
		return jsonify({"error": "Failed to fetch words {0}".format(e)}), 500

# to get the image of the template
@app.route('/api/template-image/<int:template_id>')
@login_required
def get_template_image(template_id):
	try:
		connection = get_db_connection()
		if connection is None:
			return jsonify({"error": "Database connection failed"}), 500
		cursor = connection.cursor(dictionary=True)
		cursor.execute("SELECT image_url FROM tbl_templates WHERE id = %s", (template_id,))
		template = cursor.fetchone()
		cursor.close()
		connection.close()
		if not template:
			return jsonify({"error": "Template not found"}), 404
		image_url = template['image_url']
		image_path = os.path.join(app.config['IMAGE_DIRECTORY'], image_url)
		if not os.path.exists(image_path):
			current_app.logger.error(f"Image file not found at path: {image_path}")
			return jsonify({"error": "Image file not found at {app.config['IMAGE_DIRECTORY']}"}), 404
		return send_file(image_path)
	except Exception as e:
		current_app.logger.error(f"Error fetching template image: {e}")
		return jsonify({"error": "Failed to fetch template image ex"}), 500

def check_words_in_database(words: list[str]) -> list[str]:
    found_words = []
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if connection is None:
            current_app.logger.error("Database connection failed.")
            return None
        
        cursor = connection.cursor()
        placeholders = ', '.join(['%s'] * len(words))
        query = f"SELECT title FROM tbl_words WHERE title COLLATE utf8mb4_bin IN ({placeholders})"
        
        lowercase_words = [word for word in words]
        cursor.execute(query, lowercase_words)
        
        found_words = [row[0] for row in cursor.fetchall()]
        current_app.logger.info(f"Words exist in database: {found_words}")
        
        # Check if all words were found
        if len(found_words) != len(words):
            missing_words = set(word for word in words) - set(word for word in found_words)
            current_app.logger.error(f"Some words do not exist in the database: {missing_words}")
            return None
        
        return words
    
    except mysql.connector.Error as err:
        current_app.logger.error(f"Database error: {err}")
        return None
    
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
			
# for header generation
@app.route('/generate-header', methods=['POST'])
@login_required
def generate_header():
	try:
		template_id = request.form.get('template_id')
		words = request.form.getlist('words[]')
		found_words = None
		
		if not template_id or not template_id.isdigit():
			return "Template is required", 400
		# Words are not mandatory
		if not words:
			words = []

		if len(words) > 3:
			return "Only 3 words are allowed", 400
		
		if len(words) > 0:
			found_words = check_words_in_database(words)
		else:
			found_words = []
		
		# Check if words exist in the database
		if len(words) > 0 and not found_words:
			return "Some words do not exist in the database", 400

		# Get template image URL from database
		connection = get_db_connection()
		cursor = connection.cursor(dictionary=True)
		cursor.execute('SELECT title, image_url FROM tbl_templates WHERE id = %s', (template_id,))
		template = cursor.fetchone()
		
		if not template:
			return "Template not found", 404
		
		# Get full path to the template image
		image_url = template['image_url']
		template_path = os.path.join(app.config['IMAGE_DIRECTORY'], image_url)
		
		if not os.path.exists(template_path):
			return "Template image not found", 404
		# Open the template image
		img = Image.open(template_path)
		
		# Get font
		font_path = os.path.join('static', 'fonts', 'ITCCharterCom-Regular.ttf')
		if not os.path.exists(font_path):
			return "Font not found", 404
		
		# Create a drawing context
		draw = ImageDraw.Draw(img)
		
		# Calculate font size based on image size
		font_size = 30; #max(16, int(img.height * 0.04))
		font = ImageFont.truetype(font_path, font_size)
		
		# Position for text (top right, 50px from borders)
		margin = 50
		x = img.width - margin
		y = margin

		# join the words into a single string separated by |
		_wordsArray = '   |   '.join(word.strip() for word in found_words) if len(found_words) > 0 else ''
		# Draw the words on the image
		try:
			text_width, text_height = draw.textbbox((0, 0), _wordsArray, font=font)[2:]
		except AttributeError:
			# Fall back to textsize for older Pillow versions
			text_width, text_height = draw.textsize(_wordsArray, font=font)
		draw.text((x - text_width, y), _wordsArray, font=font, fill=(0, 0, 0))  # White text
		# Save the image to a bytes buffer instead of a file
		img_buffer = io.BytesIO()
		img.save(img_buffer, format='PNG')
		img_buffer.seek(0)
		cursor.execute(
			'INSERT INTO tbl_generated_templates (template_id) VALUES (%s)',
			(template_id,)
		)
		template_generated_id = cursor.lastrowid  # Get ID of the inserted row

		# Insert each word into tbl_generated_words
		for word in words:
			cursor.execute(
				'INSERT INTO tbl_generated_words (template_generated_id, word) VALUES (%s, %s)',
				(template_generated_id, word.strip())
			)

		# Create a file name based on the template title and current date/time 
		base_name = template["title"].replace(" ", "-")
		# Get current date and time
		now = datetime.datetime.now()
		# Format the date and time as specified
		date_time_str = now.strftime("-%m-%d-%H-%M")
		connection.commit()
		connection.close()
		
		# Create a file name based on the template title and current date/time 
		base_name = template["title"].replace(" ", "_")
		# Get current date and time
		now = datetime.datetime.now()
		# Format the date and time as specified
		date_time_str = now.strftime("_%m%d%Y")
		# Return the image as a downloadable file
		return send_file(
			img_buffer,
			mimetype='image/png',
			as_attachment=True,
			download_name=f"{base_name}{date_time_str}.png"
		)
		
	except Exception as e:
		return str(e), 500

# for forbidden requests
@app.route('/forbidden')
def forbidden():
	return render_template('forbidden.html')

def prepare_flask_request(request):
	# If server is behind proxys or balancers use the HTTP_X_FORWARDED fields
	url_data = urlparse(request.url)
	return {
		'https': 'on' if request.scheme == 'https' else 'off',
		'http_host': "hgg.pwc.com",
		'server_port': url_data.port if url_data.port else ('443' if request.scheme == 'https' else '80'),
		'script_name': request.path,
		'get_data': request.args.copy(),
		'post_data': request.form.copy(),
		'query_string': request.query_string
	}

@app.route('/saml-login', methods=['GET', 'POST'])
def saml_login():
	session['user'] = {
		'username': 'Asdf',
		'email': 'asb@sd.com'
	}
	return redirect("/")

@app.route('/saml-acs', methods=['GET', 'POST'])
def authorize_saml_response():
	try:
		# analyze the SAML response and extract user information
		req = prepare_flask_request(request)
		auth = load_saml_auth(req)
		saml_response = request.form.get('SAMLResponse')
		# current_app.logger.info(f"Raw SAML Response: \n{saml_response}")
		auth.process_response()
		errors = auth.get_errors()
		if len(errors) == 0:
			current_app.logger.info(f"No errors in SAML response")
			if auth.is_authenticated():
				current_app.logger.info(f"User authenticated successfully")
				# current_app.logger.info(f"User attributes: {auth.get_attributes()}")
				# Check if the user is authenticated
				# User is authenticated, set session variables
				session['user'] = {
					'username': auth.get_attributes().get('givenname', [''])[0],
					'email': auth.get_attributes().get('pwcpreferredmail', [''])[0]
				}
				current_app.logger.info(f"User session: {session['user']}")
				# redirect to the home page
				return redirect("/")
			else:
				current_app.logger.info(f"User NOT authenticated successfully")
				return "Authentication failed", 403
		else:
			current_app.logger.info(f"Errors in SAML Response: {errors}")
			# Redirect to the next URL or the home page
			return redirect(url_for("logout"))
	except Exception as e:
		return str(e), 500
	
@app.route('/template-report')
def template_report():
    try:
        # Establish DB connection
        connection = get_db_connection()
        if connection is None:
            return render_template("error.html", message="Database connection failed"), 500

        cursor = connection.cursor()

        # Fetch filters
        from_date = request.args.get("from_date")
        to_date = request.args.get("to_date")
        page = int(request.args.get("page", 1))
        sort_by = request.args.get("sort_by", "date")  # Options: date, title, count
        order = request.args.get("order", "ASC").upper()  # ASC or DESC

        # Ensure valid sort_by and order
        valid_sort_fields = {
            "date": "date",
            "title": "t.title",
            "count": "count"
        }
        valid_orders = {"ASC", "DESC"}
        sort_column = valid_sort_fields.get(sort_by, "date")
        sort_order = "ASC" if order not in valid_orders else order

        # Pagination settings
        per_page = 10
        offset = (page - 1) * per_page

        # Build WHERE clause
        where_conditions = ["1=1"]
        query_params = []
        if from_date:
            where_conditions.append("gt.created_at >= %s")
            query_params.append(from_date)
        if to_date:
            where_conditions.append("gt.created_at <= %s")
            query_params.append(to_date)
        where_clause = " AND ".join(where_conditions)

        # Count query for pagination
        count_groups_query = f"""
            SELECT COUNT(*) FROM (
                SELECT DATE(gt.created_at), t.title
                FROM tbl_generated_templates gt
                JOIN tbl_templates t ON gt.template_id = t.id
                WHERE {where_clause}
                GROUP BY DATE(gt.created_at), t.title
            ) AS groups_count
        """
        cursor.execute(count_groups_query, query_params)
        total_records = cursor.fetchone()[0]
        total_pages = (total_records // per_page) + (1 if total_records % per_page > 0 else 0)

        # Grouped count query with dynamic ORDER BY
        grouped_query = f"""
            SELECT DATE(gt.created_at) as date, t.title, COUNT(*) as count
            FROM tbl_generated_templates gt
            JOIN tbl_templates t ON gt.template_id = t.id
            WHERE {where_clause}
            GROUP BY DATE(gt.created_at), t.title
            ORDER BY {sort_column} {sort_order}
            LIMIT %s OFFSET %s
        """
        cursor.execute(grouped_query, query_params + [per_page, offset])
        grouped_counts = cursor.fetchall()

        cursor.close()
        connection.close()

        return render_template(
            "templateReport.html",
            grouped_counts=grouped_counts,
            from_date=from_date,
            to_date=to_date,
            page=page,
            total_pages=total_pages,
            sort_by=sort_by,
            order=sort_order
        )

    except Exception as e:
        error_details = traceback.format_exc()
        current_app.logger.error(f"Error rendering template report: {e}\n{error_details}")
        return render_template("error.html", message="Internal server error occurred."), 500


@app.route('/word-report')
def word_report():
    try:
        connection = get_db_connection()
        if connection is None:
            return render_template("error.html", message="Database connection failed"), 500

        cursor = connection.cursor()

        from_date = request.args.get("from_date")
        to_date = request.args.get("to_date")
        page = int(request.args.get("page", 1))
        per_page = 10
        offset = (page - 1) * per_page

        sort_by = request.args.get('sort_by', 'date')  # Default sort by date
        order = request.args.get('order', 'ASC')  # Default order is ascending

        valid_sort_columns = ['date', 'title', 'word_count']
        if sort_by not in valid_sort_columns:
            sort_by = 'date'

        valid_orders = ['ASC', 'DESC']
        if order not in valid_orders:
            order = 'ASC'

        where_conditions = ["1=1"]
        query_params = []

        if from_date:
            where_conditions.append("gw.created_at >= %s")
            query_params.append(from_date)
        if to_date:
            where_conditions.append("gw.created_at <= %s")
            query_params.append(to_date)

        where_clause = " AND ".join(where_conditions)

        # Total record count
        count_query = f"""
            SELECT COUNT(*) FROM (
                SELECT DATE(gw.created_at), w.title
                FROM tbl_generated_words gw
                JOIN tbl_words w ON gw.word = w.title
                WHERE {where_clause}
                GROUP BY DATE(gw.created_at), w.title
            ) AS grouped_data
        """
        cursor.execute(count_query, query_params)
        total_records = cursor.fetchone()[0]
        total_pages = (total_records // per_page) + (1 if total_records % per_page > 0 else 0)

        # Fetch word title + count + date with sorting
        word_report_query = f"""
            SELECT DATE(gw.created_at) AS date, w.title, COUNT(*) AS word_count
            FROM tbl_generated_words gw
            JOIN tbl_words w ON gw.word = w.title
            WHERE {where_clause}
            GROUP BY DATE(gw.created_at), w.title
            ORDER BY {sort_by} {order}
            LIMIT %s OFFSET %s
        """
        cursor.execute(word_report_query, query_params + [per_page, offset])
        word_counts = cursor.fetchall()

        cursor.close()
        connection.close()

        return render_template("wordReport.html",
                               word_counts=word_counts,
                               from_date=from_date,
                               to_date=to_date,
                               page=page,
                               total_pages=total_pages,
                               sort_by=sort_by,
                               order=order)

    except Exception as e:
        error_details = traceback.format_exc()
        current_app.logger.error(f"Error generating word report: {e}\n{error_details}")
        return render_template("error.html", message="Internal server error occurred."), 500


# for logout
@app.route('/logout')
def logout():
	req = prepare_flask_request(request)
	auth = load_saml_auth(req)
	# Clear user session
	session.clear()
	# Get logout URL from IdP
	return redirect(auth.logout())

