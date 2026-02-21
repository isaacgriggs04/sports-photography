"""
Sports Photo Platform Prototype - Athlete Workflow

Simulates an athlete-side flow where an athlete can:
1) search for a school
2) select a sport
3) view game schedule
4) view photos from a selected game that contain them
5) purchase photos and unlock full-resolution access
"""

# ----------------------------
# Mock Data
# ----------------------------
schools = [
    {"name": "Lawrence Tech", "sports": ["Basketball", "Soccer"]},
    {"name": "Wayne State", "sports": ["Basketball", "Soccer"]},
    {"name": "Homewood Flossmoor", "sports": ["Softball", "Baseball"]},
]

games = [
    {
        "school": "Lawrence Tech",
        "sport": "Basketball",
        "date": "2026-02-08",
        "opponent": "Wayne State",
        "game_id": 1,
    },
    {
        "school": "Lawrence Tech",
        "sport": "Basketball",
        "date": "2026-02-10",
        "opponent": "Oakland",
        "game_id": 2,
    },
    {
        "school": "Wayne State",
        "sport": "Basketball",
        "date": "2026-02-09",
        "opponent": "Lawrence Tech",
        "game_id": 3,
    },
    {
        "school": "Homewood Flossmoor",
        "sport": "Softball",
        "date": "2026-04-12",
        "opponent": "Lincoln-Way East",
        "game_id": 101,
    },
    # Homewood Flossmoor Baseball 2026
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "03/16/2026", "opponent": "Whitney Young", "game_id": 201},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "03/21/2026", "opponent": "Montini", "game_id": 202},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "03/25/2026", "opponent": "Jones College Prep", "game_id": 203},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "04/06/2026", "opponent": "Lockport", "game_id": 204},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "04/10/2026", "opponent": "Bradley-Bourbonnais", "game_id": 205},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "04/11/2026", "opponent": "Morgan Park", "game_id": 206},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "04/14/2026", "opponent": "Rich Township", "game_id": 207},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "04/16/2026", "opponent": "Lincoln-Way West", "game_id": 208},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "04/18/2026", "opponent": "Brother Rice", "game_id": 209},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "04/22/2026", "opponent": "Andrew", "game_id": 210},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "04/30/2026", "opponent": "Lincoln-Way East", "game_id": 211},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "05/06/2026", "opponent": "Lincoln-Way Central", "game_id": 212},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "05/11/2026", "opponent": "Sandburg", "game_id": 213},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "05/14/2026", "opponent": "TF South", "game_id": 214},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "05/16/2026", "opponent": "De La Salle", "game_id": 215},
    {"school": "Homewood Flossmoor", "sport": "Baseball", "date": "05/20/2026", "opponent": "Stagg", "game_id": 216},
]

photos = [
    {
        "game_id": 1,
        "uploaded_by": "Alice",
        "detected_players": ["Isaac", "Jordan"],
        "image_url": "game1_photo1.jpg",
        "price": 5,
    },
    {
        "game_id": 1,
        "uploaded_by": "Alice",
        "detected_players": ["Isaac"],
        "image_url": "game1_photo2.jpg",
        "price": 5,
    },
    {
        "game_id": 2,
        "uploaded_by": "Alice",
        "detected_players": ["Jordan"],
        "image_url": "game2_photo1.jpg",
        "price": 5,
    },
    {
        "game_id": 3,
        "uploaded_by": "Bob",
        "detected_players": ["Alex"],
        "image_url": "game3_photo1.jpg",
        "price": 4,
    },
]

# Tracks athlete-specific purchases:
# {"athlete": "<name>", "photo": "<image_url>"}
purchases = []


# ----------------------------
# Functions for Athlete Workflow
# ----------------------------
def search_school(name):
    for school in schools:
        if name.lower() in school["name"].lower():
            return school
    return None


def list_sports(school):
    return school["sports"]


def view_schedule(school_name, sport_name):
    return [g for g in games if g["school"] == school_name and g["sport"] == sport_name]


def has_purchased(athlete_name, image_url):
    return any(
        purchase["athlete"] == athlete_name and purchase["photo"] == image_url
        for purchase in purchases
    )


def view_game_photos(game_id, athlete_name):
    game_photos = [
        p for p in photos if p["game_id"] == game_id and athlete_name in p["detected_players"]
    ]
    if not game_photos:
        print(f"No photos found for {athlete_name} in this game.")
        return

    print(f"Photos for game {game_id}:")
    for photo in game_photos:
        purchased = has_purchased(athlete_name, photo["image_url"])
        status = "Full-res unlocked" if purchased else "Preview (watermarked)"
        print(f" - {photo['image_url']} | {status} | ${photo['price']}")


def purchase_photo(athlete_name, image_url):
    for photo in photos:
        is_detected = athlete_name in photo["detected_players"]
        if photo["image_url"] == image_url and is_detected:
            if has_purchased(athlete_name, image_url):
                print(f"{athlete_name} already purchased {image_url}.")
                return
            purchases.append({"athlete": athlete_name, "photo": image_url})
            print(f"{athlete_name} purchased {image_url}.")
            return
    print("Photo not found or athlete not detected in this photo.")


def run_demo():
    # Athlete searches for their school
    athlete_name = "Isaac"
    school = search_school("Lawrence Tech")
    if not school:
        print("School not found.")
        return

    print(f"{athlete_name} selected school: {school['name']}")
    sports = list_sports(school)
    print("Available sports:", sports)

    # Athlete selects sport
    sport = "Basketball"
    print(f"{athlete_name} selected sport: {sport}")

    # Athlete views schedule
    schedule = view_schedule(school["name"], sport)
    print("Game schedule:")
    for game in schedule:
        print(
            f" - Game ID: {game['game_id']} | Date: {game['date']} | Opponent: {game['opponent']}"
        )

    # Athlete selects a game
    selected_game_id = 1
    print(f"\nViewing photos for Game ID: {selected_game_id}")
    view_game_photos(selected_game_id, athlete_name)

    # Simulate purchasing a photo
    print("\nPurchasing a photo...")
    purchase_photo(athlete_name, "game1_photo1.jpg")

    # View photos again to see updated status
    print("\nUpdated gallery after purchase:")
    view_game_photos(selected_game_id, athlete_name)


if __name__ == "__main__":
    run_demo()
