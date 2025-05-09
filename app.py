from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify
import pandas as pd
import os
import datetime
import re
from sqlalchemy import create_engine
from jinja2 import Environment, FileSystemLoader
import pdfkit
import tempfile
from sqlalchemy import text

app = Flask(__name__)
UPLOAD_FOLDER = "data"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Neon接続
connection_string = "postgresql://neondb_owner:npg_dFsLfMg6e5Rj@ep-holy-water-a1hs2b11-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
engine = create_engine(connection_string)

env = Environment(loader=FileSystemLoader("templates"))


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        file = request.files["file"]
        filename = file.filename
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(file_path)

        match = re.search(r"(\d{8})", filename)
        if not match:
            return "ファイル名に日付 (YYYYMMDD) が含まれていません", 400
        base_date = datetime.datetime.strptime(match.group(1), "%Y%m%d").date()

        weekdays_fixed = ["数量_月", "数量_火", "数量_水", "数量_木", "数量_金", "数量_土", "数量_日"]
        used_cols = ["商品名称", "出荷まとめ名称", "産地", "規格", "入数", "形態"] + weekdays_fixed

        df = pd.read_excel(file_path)[used_cols]

        records = []
        for _, row in df.iterrows():
            for idx, day_col in enumerate(weekdays_fixed):
                val = row[day_col]
                if pd.isna(val):
                    continue
                try:
                    quantity = int(val)
                    unit_count = int(row["入数"])
                    total_quantity = quantity * unit_count
                except:
                    quantity = int(val)
                    unit_count = None
                    total_quantity = None
                records.append({
                    "shipment_date": base_date + datetime.timedelta(days=idx),
                    "weekday": day_col.replace("数量_", ""),
                    "ship_to": row["出荷まとめ名称"],
                    "product": row["商品名称"],
                    "origin": row["産地"],
                    "spec": row["規格"],
                    "unit_count": unit_count,
                    "form": row["形態"],
                    "quantity": quantity,
                    "total_quantity": total_quantity
                })

        shipment_df = pd.DataFrame(records)
        shipment_df['key'] = shipment_df[['ship_to', 'product', 'spec', 'origin']].agg('|'.join, axis=1)

        master_df = pd.read_sql("SELECT ship_to, product, spec, origin FROM master_specifications", engine)
        master_df['key'] = master_df[['ship_to', 'product','spec','origin']].fillna('').agg('|'.join, axis=1)

        unregistered_df = shipment_df.loc[~shipment_df['key'].isin(master_df['key'])].drop_duplicates('key')

        return render_template(
            "upload.html",
            unregistered_rows=unregistered_df.to_dict(orient="records"),
            uploaded_filename=filename,
            success=(unregistered_df.empty)
        )

    return render_template("upload.html", unregistered_rows=None, success=False)

@app.route("/register_missing", methods=["POST"])
def register_missing():
    def normalize_jan(x):
        if not x or not str(x).strip().isdigit():
            return None
        s = str(x).strip()
        return s.zfill(6) if len(s) == 5 else s

    df = pd.DataFrame({
        'ship_to': request.form.getlist('ship_to[]'),
        'product': request.form.getlist('product[]'),
        'spec': request.form.getlist('spec[]'),
        'origin': request.form.getlist('origin[]'),
        'bag': request.form.getlist('bag[]'),
        'label_front': request.form.getlist('label_front[]'),
        'label_back': request.form.getlist('label_back[]'),
        'other1': request.form.getlist('other1[]'),
        'other2': request.form.getlist('other2[]'),
        'jan_code': [normalize_jan(x) for x in request.form.getlist('jan_code[]')],
        'type': request.form.getlist('type[]'),
        'bundle_count': request.form.getlist('bundle_count[]'),
    })
    df = df.where(pd.notnull(df), None)  # 空欄→None
    df.to_sql("master_specifications", engine, if_exists="append", index=False)
    return redirect(url_for("upload"))

@app.route("/register_orders", methods=["POST"])
def register_orders():
    filename = request.form["source_file"]
    file_path = os.path.join(UPLOAD_FOLDER, filename)

    match = re.search(r"(\d{8})", filename)
    base_date = datetime.datetime.strptime(match.group(1), "%Y%m%d").date()

    weekdays_fixed = ["数量_月", "数量_火", "数量_水", "数量_木", "数量_金", "数量_土", "数量_日"]
    used_cols = ["商品名称", "出荷まとめ名称", "産地", "規格", "入数", "形態", "配送便"] + weekdays_fixed

    df = pd.read_excel(file_path)[used_cols]

    records = []
    for _, row in df.iterrows():
        for idx, day_col in enumerate(weekdays_fixed):
            val = row[day_col]
            if pd.isna(val):
                continue
            try:
                quantity = int(val)
                unit_count = int(row["入数"])
                total_quantity = quantity * unit_count
            except:
                quantity = int(val)
                unit_count = None
                total_quantity = None
            records.append({
                "shipment_date": base_date + datetime.timedelta(days=idx),
                "weekday": day_col.replace("数量_", ""),
                "ship_to": row["出荷まとめ名称"],
                "product": row["商品名称"],
                "origin": row["産地"],
                "spec": row["規格"],
                "unit_count": unit_count,
                "form": row["形態"],
                "quantity": quantity,
                "total_quantity": total_quantity,
                "delivery": row["配送便"]
            })


    df_final = pd.DataFrame(records)

    # 🔽 master_specifications から補完
    master_df = pd.read_sql("SELECT * FROM master_specifications", engine)
    master_df["key"] = master_df[["ship_to", "product", "spec", "origin"]].fillna("").agg("|".join, axis=1)
    df_final["key"] = df_final[["ship_to", "product", "spec", "origin"]].fillna("").agg("|".join, axis=1)

    df_final = df_final.merge(
        master_df[["key", "bag", "label_front", "label_back", "other1", "other2", "jan_code", "type", "bundle_count"]],
        on="key", how="left"
    )
    df_final.drop(columns=["key"], inplace=True)

    with engine.begin() as conn:
        result = conn.execute(text("SELECT MAX(shipment_no) FROM shipment_orders"))
        max_no = result.scalar() or 0
        df_final["shipment_no"] = list(range(max_no + 1, max_no + 1 + len(df_final)))

        df_final.to_sql("shipment_orders", conn, if_exists="append", index=False)

    return render_template("upload.html", unregistered_rows=None, uploaded_filename=None, success=True)



@app.route("/cards_generate", methods=["POST"])
def cards_generate():
    selected_ids = request.form.getlist("selected_ids")
    if not selected_ids:
        return "📛 出力対象が選択されていません。", 400

    ids_str = ",".join(selected_ids)
    query = f"""
        SELECT * FROM shipment_orders
        WHERE id IN ({ids_str})
        ORDER BY shipment_no
    """
    df = pd.read_sql(query, engine)

    # 🔽 ここで None を空文字に置換
    df = df.fillna("")

    # display_date を追加
    df["display_date"] = df["shipment_date"].apply(lambda x: x.strftime("%Y/%m/%d"))

    # テンプレート読み込み・HTML構築（←ここが重要！）
    template = env.get_template("card_templates.html")
    html = template.render(rows=df.to_dict(orient="records"))

    # wkhtmltopdf のパスを明示
    config = pdfkit.configuration(
        wkhtmltopdf=r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe"
    )

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmpfile:
        pdfkit.from_string(
            html,
            tmpfile.name,
            configuration=config,
            options={"enable-local-file-access": ""}
        )

        # printed = TRUE に更新
        with engine.begin() as conn:
            conn.execute(
                text(f"UPDATE shipment_orders SET printed = TRUE WHERE id IN ({ids_str})")
            )

        return send_file(tmpfile.name, as_attachment=True, download_name="蔵出しカード.pdf")

@app.route("/cards_select", methods=["GET", "POST"])
def cards_select():
    if request.method == "POST":
        week_str = request.form.get("target_week")
        type_ = request.form.get("target_type") or ""
        only_unprinted = request.form.get("only_unprinted")

        # 安定した週→月曜日変換
        year, week = week_str.split("-W")
        monday = datetime.datetime.strptime(f"{year} {week} 1", "%G %V %u").date()
        sunday = monday + datetime.timedelta(days=6)

        query = "SELECT * FROM shipment_orders WHERE shipment_date BETWEEN :monday AND :sunday"
        params = {"monday": monday, "sunday": sunday}

        if type_:
            query += " AND type = :type"
            params["type"] = type_

        if only_unprinted:
            query += " AND printed = FALSE"

        query += " ORDER BY shipment_no"

        df = pd.read_sql(text(query), engine, params=params)

        return render_template("cards_select.html",
                               orders=df.to_dict(orient="records"),
                               target_week=week_str,
                               target_type=type_,
                               only_unprinted=only_unprinted)

    return render_template("cards_select.html", orders=None)


@app.route("/orders")
def orders():
    date = request.args.get("date")
    ship_to = request.args.get("ship_to")
    no = request.args.get("no")
    type_ = request.args.get("type")
    printed = request.args.get("printed")

    query = "SELECT * FROM shipment_orders WHERE TRUE"
    filters = []

    if date:
        dt = pd.to_datetime(date)
        monday = dt - pd.Timedelta(days=dt.weekday())
        sunday = monday + pd.Timedelta(days=6)
        filters.append(f"shipment_date BETWEEN '{monday.date()}' AND '{sunday.date()}'")

    if ship_to:
        filters.append(f"ship_to ILIKE '%%{ship_to}%%'")

    if no:
        filters.append(f"shipment_no = {no}")
    if type_:
        filters.append(f"type = '{type_}'")
    if printed == "false":
        filters.append("printed = FALSE")

    if filters:
        query += " AND " + " AND ".join(filters)

    df = pd.read_sql(query, engine)
    return render_template("orders.html", rows=df.to_dict(orient="records"))


@app.route("/order/<int:shipment_no>")
def order_detail(shipment_no):
    query = f"SELECT * FROM shipment_orders WHERE shipment_no = {shipment_no}"
    df = pd.read_sql(query, engine)
    if df.empty:
        return f"📛 No.{shipment_no} は見つかりませんでした。", 404
    row = df.iloc[0].to_dict()
    return render_template("order_detail.html", row=row)

@app.route("/add_order", methods=["GET", "POST"])
def add_order():
    if request.method == "POST":
        data = request.form
        ship_to = data.get("ship_to")
        product = data.get("product")
        spec = data.get("spec")
        origin = data.get("origin")
        shipment_date = data.get("shipment_date")
        unit_count = int(data.get("unit_count"))
        quantity = int(data.get("quantity"))
        total_quantity = quantity * unit_count
        form_ = data.get("form")
        delivery = data.get("delivery") or ""

        # マスタ照合
        query = """
        SELECT * FROM master_specifications 
        WHERE ship_to = %(ship_to)s AND product = %(product)s AND spec = %(spec)s AND origin = %(origin)s
        """
        params = {
            "ship_to": ship_to,
            "product": product,
            "spec": spec,
            "origin": origin
        }
        master = pd.read_sql(query, engine, params=params)

        if master.empty:
            return "📛 該当マスタが見つかりませんでした", 400

        m = master.iloc[0]
        label_front = m.get("label_front")
        label_back = m.get("label_back")
        bag = m.get("bag")
        jan_code = m.get("jan_code")
        bundle_count = m.get("bundle_count")
        other1 = m.get("other1")
        other2 = m.get("other2")

        # 蔵出しNoの最大値を取得して+1
        max_no_df = pd.read_sql("SELECT MAX(shipment_no) FROM shipment_orders", engine)
        next_no = int(max_no_df.iloc[0, 0] or 0) + 1

        insert_sql = text("""
            INSERT INTO shipment_orders (
                shipment_no, shipment_date, weekday, ship_to, product, origin, spec, quantity,
                unit_count, total_quantity, form, label_front, label_back, bag, jan_code, type, bundle_count,
                delivery, other1, other2, printed
            ) VALUES (
                :shipment_no, :shipment_date, :weekday, :ship_to, :product, :origin, :spec, :quantity,
                :unit_count, :total_quantity, :form, :label_front, :label_back, :bag, :jan_code, '追加', :bundle_count,
                :delivery, :other1, :other2, FALSE
            )
        """)




        shipment_date = pd.to_datetime(shipment_date)
        weekday = shipment_date.strftime("%a")

        with engine.begin() as conn:
            conn.execute(insert_sql, {
                "shipment_no": next_no,
                "shipment_date": shipment_date.date(),
                "weekday": weekday,
                "ship_to": ship_to,
                "product": product,
                "origin": origin,
                "spec": spec,
                "quantity": quantity,
                "unit_count": unit_count,
                "total_quantity": total_quantity,
                "form": form_,
                "label_front": label_front,
                "label_back": label_back,
                "bag": bag,
                "jan_code": jan_code,
                "bundle_count": bundle_count,
                "delivery": delivery,
                "other1": other1,
                "other2": other2
            })

        df = pd.read_sql("SELECT DISTINCT ship_to FROM master_specifications ORDER BY ship_to", engine)
        return render_template("add_order.html", ship_tos=df.ship_to.tolist(), success=True)


    # GET: フォーム表示用にユニークな選択肢を取得
    df = pd.read_sql("SELECT DISTINCT ship_to FROM master_specifications ORDER BY ship_to", engine)
    return render_template("add_order.html", ship_tos=df.ship_to.tolist())

@app.route("/api/master_options")
def master_options():
    args = request.args
    df = pd.read_sql("SELECT * FROM master_specifications", engine)

    # 順次フィルタ（ship_to→product→spec→originの順で絞る）
    for key in ["ship_to", "product", "spec"]:
        if key in args:
            df = df[df[key] == args.get(key)]

    # 次に返すべきキーを判定
    if "ship_to" in args and "product" not in args:
        next_key = "product"
    elif "product" in args and "spec" not in args:
        next_key = "spec"
    elif "spec" in args and "origin" not in args:
        next_key = "origin"
    else:
        return jsonify([])

    options = sorted(df[next_key].dropna().unique().tolist())
    return jsonify(options)


@app.route("/master_specifications", methods=["GET", "POST"])
def master_specifications():
    if request.method == "POST":
        df = pd.read_sql("SELECT * FROM master_specifications ORDER BY id", engine)

        for i in range(len(df)):
            id_ = request.form.get(f"id_{i}")
            bag = request.form.get(f"bag_{i}")
            label_front = request.form.get(f"label_front_{i}")
            label_back = request.form.get(f"label_back_{i}")
            other1 = request.form.get(f"other1_{i}")
            other2 = request.form.get(f"other2_{i}")
            jan_code = request.form.get(f"jan_code_{i}")
            type_ = request.form.get(f"type_{i}")
            bundle_count = request.form.get(f"bundle_count_{i}")

            sql = """
            UPDATE master_specifications SET
                bag = %s,
                label_front = %s,
                label_back = %s,
                other1 = %s,
                other2 = %s,
                jan_code = %s,
                type = %s,
                bundle_count = %s
            WHERE id = %s
            """
            engine.execute(sql, (bag, label_front, label_back, other1, other2, jan_code, type_, bundle_count, id_))

        # 新規追加処理
        for i in range(3):
            ship_to = request.form.get(f"new_ship_to_{i}")
            product = request.form.get(f"new_product_{i}")
            spec = request.form.get(f"new_spec_{i}")
            origin = request.form.get(f"new_origin_{i}")
            if not ship_to or not product or not spec or not origin:
                continue

            bag = request.form.get(f"new_bag_{i}")
            label_front = request.form.get(f"new_label_front_{i}")
            label_back = request.form.get(f"new_label_back_{i}")
            other1 = request.form.get(f"new_other1_{i}")
            other2 = request.form.get(f"new_other2_{i}")
            jan_code = request.form.get(f"new_jan_code_{i}")
            type_ = request.form.get(f"new_type_{i}")
            bundle_count = request.form.get(f"new_bundle_count_{i}")

            sql = """
            INSERT INTO master_specifications
            (ship_to, product, spec, origin, bag, label_front, label_back, other1, other2, jan_code, type, bundle_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            engine.execute(sql, (ship_to, product, spec, origin, bag, label_front, label_back, other1, other2, jan_code, type_, bundle_count))

        return redirect("/master_specifications")

    df = pd.read_sql("SELECT * FROM master_specifications ORDER BY ship_to, product, origin", engine)
    grouped = {}
    for row in df.to_dict(orient="records"):
        ship_to = row["ship_to"] or "(未設定)"
        grouped.setdefault(ship_to, []).append(row)

    return render_template("master_specifications.html", grouped=grouped)

@app.route("/")
def index():
    return redirect("/orders")



if __name__ == "__main__":
    app.run(debug=True)

