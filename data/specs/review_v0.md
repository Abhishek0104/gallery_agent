# Episode specs v0 — review

50 specs. Per spec: persona, path, motivation, steps (args → outcome), user turns.

## ep_0001 · P058 · Lakshmi (persona_06, india) · collage_max 6 · effects sepia, cool · turns one_per_turn
*Lakshmi wants to create a vintage-style album of her sister painting a clay pot in Kochi. She applies a sepia effect to her favorite shots and saves them to a new album.*

- 1. `search_images(query="painting a clay pot", people=["sister"])` → {"count": 36}
- 2. `search_images(query="painting a clay pot", people=["sister"], location="Kochi")` → {"count": 30} *(refines 1)*
- 3. [user selects 22 from r2 → r3]
- 4. `apply_effect(images="r3", effect="sepia")` → {"count": 22}
- 5. `move_to_album(images="r4", album="Vintage Pottery")` → {"count": 22, "album": "Vintage Pottery", "created": true}
- turns: [[1], [2], [3, 4], [5]]
- query hints: step 1: relational/medium e.g. ['holding a small rabbit', 'passing a soccer ball', 'looking through a telescope']

## ep_0002 · P072 · Joanne (persona_04, singapore) · collage_max 4 · effects sepia, warm, cool, black_and_white · turns one_message
*Joanne wants to preview how two selected photos look side by side for a potential layout, but only needs a quick look, so she asks to generate a collage and then delete it immediately.*

- 0. [starts with 2 photos selected → r0]
- 1. `make_collage(images="r0")` → {"count": 1}
- 2. `delete_images(images="r1")` → {"count": 1}
- turns: [[1, 2]]

## ep_0003 · P060 · Marcus (persona_03, us) · collage_max 9 · effects cool, sepia, black_and_white, warm · turns one_per_turn
*Marcus wants to make a warm-toned collage of photos of himself from 2023 to share with his wife Elena.*

- 1. `search_images(people=["me"])` → {"count": 13}
- 2. `search_images(people=["me"], date="in 2023")` → {"count": 7} *(refines 1)*
- 3. `make_collage(images="r2")` → {"count": 1}
- 4. `apply_effect(images="r3", effect="warm")` → {"count": 1}
- turns: [[1], [2], [3], [4]]

## ep_0004 · P086 · Brenda (persona_18, us) · collage_max 4 · effects cool, sepia · turns one_per_turn
*Brenda wants to give the two photos she selected an antique look with a sepia filter and combine them into a collage for her quilting project inspiration.*

- 0. [starts with 2 photos selected → r0]
- 1. `apply_effect(images="r0", effect="sepia")` → {"count": 2}
- 2. `make_collage(images="r1")` → {"count": 1}
- 3. `move_to_album(images="r2", album="Vintage Collages")` → {"count": 1, "album": "Vintage Collages", "created": true}
- turns: [[1], [2], [3]]

## ep_0005 · P066 · Connor (persona_09, uk) · collage_max 12 · effects black_and_white, sepia, cool · turns mixed
*Connor wants to see how photos of his wife from Snowdonia last summer look in black and white, but after seeing the edits, he dislikes how they turned out and deletes them.*

- 1. `search_images(location="Snowdonia", date="last summer")` → {"count": 39}
- 2. `search_images(location="Snowdonia", date="last summer", people=["wife"])` → {"count": 34} *(refines 1)*
- 3. `apply_effect(images="r2", effect="black_and_white")` → {"count": 34}
- 4. `delete_images(images="r3")` → {"count": 34}
- turns: [[1], [2], [3], [4]]

## ep_0006 · P076 · Kevin (persona_20, us) · collage_max 6 · effects warm, black_and_white, sepia, cool · turns one_message
*Kevin wants to try applying a cool tone effect to the nine photos he currently has selected. After seeing the results, he only likes how three of them turned out, so he deletes the other six edited copies.*

- 0. [starts with 9 photos selected → r0]
- 1. `apply_effect(images="r0", effect="cool")` → {"count": 9}
- 2. [user selects 6 from r1 → r2]
- 3. `delete_images(images="r2")` → {"count": 6}
- turns: [[1], [2, 3]]

## ep_0007 · P025 · Connor (persona_09, uk) · collage_max 9 · effects black_and_white, cool · turns mixed
*Connor wants to pick out some photos of himself from the Lake District and convert them to black and white for a new photo collection.*

- 1. `search_images(people=["me"], location="Lake District")` → {"count": 15}
- 2. [user selects 7 from r1 → r2]
- 3. `apply_effect(images="r2", effect="black_and_white")` → {"count": 7}
- 4. `move_to_album(images="r3", album="Lake District B&W")` → {"count": 7, "album": "Lake District B&W", "created": true}
- turns: [[1], [2, 3, 4]]

## ep_0008 · P024 · Aditya (persona_12, india) · collage_max 9 · effects cool, warm · turns one_per_turn
*Aditya experimented with applying a warm filter to scanned apartment rental lease documents to check if it made the text more legible, but he deleted the copies after disliking the discolored look.*

- 1. `search_images(query="scanned apartment rental lease document")` → {"count": 32}
- 2. [user selects 9 from r1 → r2]
- 3. `apply_effect(images="r2", effect="warm")` → {"count": 9}
- 4. `delete_images(images="r3")` → {"count": 9}
- turns: [[1], [2, 3], [4]]
- query hints: step 1: documents/long e.g. ['scanned copy of signed contract', 'framed school certificate of completion', 'income tax return acknowledgement receipt']

## ep_0009 · P082 · Kunal (persona_14, india) · collage_max 12 · effects cool, black_and_white, warm · turns one_per_turn
*Kunal wants to turn the photos he has selected into a collage, give it a stylish cool tone, and save the result into a new album for his collages.*

- 0. [starts with 11 photos selected → r0]
- 1. `make_collage(images="r0")` → {"count": 1}
- 2. `apply_effect(images="r1", effect="cool")` → {"count": 1}
- 3. `move_to_album(images="r2", album="Collages")` → {"count": 1, "album": "Collages", "created": true}
- turns: [[1], [2], [3]]

## ep_0010 · P061 · Lucas (persona_13, australia) · collage_max 4 · effects warm, black_and_white, sepia, cool · turns one_message
*Lucas is looking through Queenstown photos to find pictures of Eleni and his cousin. He wants to make a collage of the two of them and save it into a new album.*

- 1. `search_images(location="Queenstown")` → {"count": 20}
- 2. `search_images(location="Queenstown", people=["Eleni", "cousin"])` → {"count": 2} *(refines 1)*
- 3. `make_collage(images="r2")` → {"count": 1}
- 4. `move_to_album(images="r3", album="Queenstown Collages")` → {"count": 1, "album": "Queenstown Collages", "created": true}
- turns: [[1], [2, 3, 4]]

## ep_0011 · P079 · Kunal (persona_14, india) · collage_max 6 · effects cool, warm, black_and_white, sepia · turns one_message
*Kunal wanted to test a black-and-white effect on his eight selected photos for street photography, but decided against keeping them and had the newly created copies deleted right away.*

- 0. [starts with 8 photos selected → r0]
- 1. `apply_effect(images="r0", effect="black_and_white")` → {"count": 8}
- 2. `delete_images(images="r1")` → {"count": 8}
- turns: [[1, 2]]

## ep_0012 · P013 · Nitika (persona_16, india) · collage_max 6 · effects warm, cool, black_and_white · turns one_per_turn
*Nitika wants to explore her passion for street photography by converting a few of her favorite shots taken in Mysuru into classic black-and-white edits.*

- 1. `search_images(location="Mysuru")` → {"count": 26}
- 2. [user selects 5 from r1 → r2]
- 3. `apply_effect(images="r2", effect="black_and_white")` → {"count": 5}
- turns: [[1], [2, 3]]

## ep_0013 · P083 · Nitika (persona_16, india) · collage_max 4 · effects sepia, warm, black_and_white · turns mixed
*Nitika wants to convert eight photos she selected into black and white to see how they look. She then tests combining two of the monochrome images into a collage, but decides she does not like the result and deletes it.*

- 0. [starts with 8 photos selected → r0]
- 1. `apply_effect(images="r0", effect="black_and_white")` → {"count": 8}
- 2. [user selects 2 from r1 → r2]
- 3. `make_collage(images="r2")` → {"count": 1}
- 4. `delete_images(images="r3")` → {"count": 1}
- turns: [[1], [2, 3, 4]]

## ep_0014 · P039 · Lucas (persona_13, australia) · collage_max 4 · effects warm, black_and_white · turns one_message
*Lucas wants to look through photos of flipping sausages on a barbecue grill. He then narrows the selection down to those taken last weekend to make a collage to share with his cousin Youssef.*

- 1. `search_images(query="flipping sausages on a barbecue grill")` → {"count": 22}
- 2. `search_images(query="flipping sausages on a barbecue grill", date="last weekend")` → {"count": 2} *(refines 1)*
- 3. `make_collage(images="r2")` → {"count": 1}
- turns: [[1], [2, 3]]
- query hints: step 1: actions/long e.g. ['pouring a glass of red wine', 'riding a scooter down a hill', 'people dancing on a dance floor']

## ep_0015 · P019 · Lucas (persona_13, australia) · collage_max 9 · effects black_and_white, warm, sepia, cool · turns one_per_turn
*Lucas wants to look through photos taken in Adelaide from last month, then narrows the search down to pictures featuring his sons.*

- 1. `search_images(location="Adelaide", date="last month")` → {"count": 40}
- 2. `search_images(location="Adelaide", date="last month", people=["son"])` → {"count": 14} *(refines 1)*
- turns: [[1], [2]]

## ep_0016 · P012 · Robert (persona_15, us) · collage_max 6 · effects black_and_white, warm, cool, sepia · turns mixed
*Robert wants to clean out duplicate and blurry photos of himself holding fish to free up storage space.*

- 1. `search_images(query="holding fish", people=["me"])` → {"count": 30}
- 2. [user selects 17 from r1 → r2]
- 3. `delete_images(images="r2")` → {"count": 17}
- turns: [[1], [2, 3]]
- query hints: step 1: relational/short e.g. ['walking dog', 'wearing sunglasses', 'petting cat']

## ep_0017 · P069 · Ishaan (persona_10, india) · collage_max 6 · effects sepia, black_and_white, warm, cool · turns mixed
*Ishaan picked out 14 blurry and duplicate shots while reviewing his gallery and wants to delete them to free up phone storage.*

- 0. [starts with 14 photos selected → r0]
- 1. `delete_images(images="r0")` → {"count": 14}
- turns: [[1]]

## ep_0018 · P006 · Shruti (persona_05, india) · collage_max 4 · effects cool, sepia, warm, black_and_white · turns one_per_turn
*Shruti wants to find out the name of the riverside cafe in Rishikesh from her photos and organize one of the pictures into a new album for that vacation.*

- 1. `ask_gallery(question="What was the name of the riverside cafe in Rishikesh?")` → {"answer": "Little Buddha Cafe.", "count": 4}
- 2. [user selects 1 from r1 → r2]
- 3. `move_to_album(images="r2", album="Rishikesh Trip")` → {"count": 1, "album": "Rishikesh Trip", "created": true}
- turns: [[1], [2, 3]]

## ep_0019 · P051 · Vikram (persona_17, india) · collage_max 4 · effects cool, sepia, black_and_white · turns one_per_turn
*Vikram wants to make a monochrome collage of his friends cuddling Jalebi to share in their group chat, but he is unhappy with the final layout and decides to delete it.*

- 1. `search_images(query="cuddling", people=["friend", "Jalebi"])` → {"count": 2}
- 2. `apply_effect(images="r1", effect="black_and_white")` → {"count": 2}
- 3. `make_collage(images="r2")` → {"count": 1}
- 4. `delete_images(images="r3")` → {"count": 1}
- turns: [[1], [2], [3], [4]]
- query hints: step 1: relational/short e.g. ['kissing', 'with bicycle', 'peace sign']

## ep_0020 · P074 · Lakshmi (persona_06, india) · collage_max 6 · effects sepia, warm · turns one_per_turn
*Lakshmi has selected four photos of her recent baking projects to make a collage and wants to save the finished image into her Recipes album.*

- 0. [starts with 4 photos selected → r0]
- 1. `make_collage(images="r0")` → {"count": 1}
- 2. `move_to_album(images="r1", album="Recipes")` → {"count": 1, "album": "Recipes", "created": false}
- turns: [[1], [2]]

## ep_0021 · P021 · Shruti (persona_05, india) · collage_max 12 · effects cool, sepia, warm, black_and_white · turns one_per_turn
*Shruti wants to create a vintage-style collage from photos of concert tickets to share with Neha, applying a sepia effect for an old-school look.*

- 1. `search_images(query="concert ticket")` → {"count": 35}
- 2. [user selects 8 from r1 → r2]
- 3. `make_collage(images="r2")` → {"count": 1}
- 4. `apply_effect(images="r3", effect="sepia")` → {"count": 1}
- turns: [[1], [2, 3], [4]]
- query hints: step 1: documents/short e.g. ['handwritten recipe', 'pan card', 'receipt']

## ep_0022 · P002 · Swathi (persona_11, india) · collage_max 6 · effects warm, sepia, cool, black_and_white · turns one_message
*Swathi wants to find pictures of Pranav and Golu with a playful puppy to show Vivek that the boys are gentle enough to adopt a pet.*

- 1. `search_images(query="playful puppy", people=["Pranav", "Golu"])` → {"count": 23}
- turns: [[1]]
- query hints: step 1: pets/short e.g. ['cat toys', 'sleeping puppy', 'dog running']

## ep_0023 · P009 · Vikram (persona_17, india) · collage_max 9 · effects cool, warm, sepia · turns one_message
*Vikram wants to check if he has photos of tigers in Ranthambore and give them a warm effect to enhance the golden tones of the wildlife shots.*

- 1. `ask_gallery(question="Do I have any photos of tigers in Ranthambore?")` → {"answer": "Yes, you have 2 photos of tigers in Ranthambore.", "count": 2}
- 2. `apply_effect(images="r1", effect="warm")` → {"count": 2}
- turns: [[1], [2]]

## ep_0024 · P004 · Kevin (persona_20, us) · collage_max 4 · effects warm, sepia · turns one_message
*Kevin wants to check when he built his walnut dining table and cleans out two blurry progress photos from the results.*

- 1. `ask_gallery(question="When was the walnut dining table built?")` → {"answer": "The walnut dining table was built between November 4 and November 12, 2022.", "count": 4}
- 2. [user selects 2 from r1 → r2]
- 3. `delete_images(images="r2")` → {"count": 2}
- turns: [[1], [2, 3]]

## ep_0025 · P003 · Vikram (persona_17, india) · collage_max 4 · effects sepia, black_and_white, cool, warm · turns mixed
*Vikram wants to see if he has photos of tigers in Ranthambore to make a collage from two of his favorite shots.*

- 1. `ask_gallery(question="Do I have photos of tigers in Ranthambore?")` → {"answer": "Yes, you have 3 photos of tigers in Ranthambore.", "count": 3}
- 2. [user selects 2 from r1 → r2]
- 3. `make_collage(images="r2")` → {"count": 1}
- turns: [[1], [2, 3]]

## ep_0026 · P075 · Callum (persona_02, uk) · collage_max 4 · effects black_and_white, cool, sepia, warm · turns mixed
*Callum wants to apply a cool tone effect to the photos he has selected and then combine two of the edited shots into a collage to send to Priya.*

- 0. [starts with 5 photos selected → r0]
- 1. `apply_effect(images="r0", effect="cool")` → {"count": 5}
- 2. [user selects 2 from r1 → r2]
- 3. `make_collage(images="r2")` → {"count": 1}
- turns: [[1], [2, 3]]

## ep_0027 · P057 · Faridah (persona_19, india) · collage_max 6 · effects cool, sepia, black_and_white · turns mixed
*Faridah looks for photos of Javed feeding pigeons from their trip to Ajmer last Christmas to apply a cool filter to them. Unhappy with how the cool tone turns out on the selected shots, she deletes the newly created copies.*

- 1. `search_images(query="feeding pigeons", people=["Javed"], location="Ajmer")` → {"count": 32}
- 2. `search_images(query="feeding pigeons", people=["Javed"], location="Ajmer", date="last Christmas")` → {"count": 10} *(refines 1)*
- 3. [user selects 8 from r2 → r3]
- 4. `apply_effect(images="r3", effect="cool")` → {"count": 8}
- 5. `delete_images(images="r4")` → {"count": 8}
- turns: [[1], [2], [3, 4], [5]]
- query hints: step 1: actions/short e.g. ['packing luggage', 'cutting cake', 'tying turban']

## ep_0028 · P016 · Kunal (persona_14, india) · collage_max 9 · effects sepia, warm, cool, black_and_white · turns mixed
*Kunal wants to free up storage space on his phone, so he decides to find and delete all photos taken in Manali after archiving them to his computer.*

- 1. `search_images(location="Manali")` → {"count": 7}
- 2. `delete_images(images="r1")` → {"count": 7}
- turns: [[1], [2]]

## ep_0029 · P073 · Swathi (persona_11, india) · collage_max 4 · effects black_and_white, sepia, cool, warm · turns one_message
*Swathi wants to turn the three photos she has selected into a collage and apply a cool filter to it before sharing it with Vivek.*

- 0. [starts with 3 photos selected → r0]
- 1. `make_collage(images="r0")` → {"count": 1}
- 2. `apply_effect(images="r1", effect="cool")` → {"count": 1}
- turns: [[1, 2]]

## ep_0030 · P065 · Leila (persona_07, uae) · collage_max 6 · effects warm, black_and_white, sepia · turns one_per_turn
*Leila wants to make a warm-toned collage of photos of her husband in Istanbul from last Eid to surprise him with a digital keepsake.*

- 1. `search_images(people=["husband"], location="Istanbul")` → {"count": 17}
- 2. `search_images(people=["husband"], location="Istanbul", date="last Eid")` → {"count": 5} *(refines 1)*
- 3. `apply_effect(images="r2", effect="warm")` → {"count": 5}
- 4. `make_collage(images="r3")` → {"count": 1}
- turns: [[1], [2], [3], [4]]

## ep_0031 · P050 · Riya (persona_08, india) · collage_max 9 · effects warm, sepia, black_and_white · turns one_message
*Riya wants to convert photos of handwritten exam revision notes into clean black-and-white versions and make a compact study collage to keep in her Notes and Syllabi album.*

- 1. `search_images(query="handwritten exam revision notes")` → {"count": 38}
- 2. `apply_effect(images="r1", effect="black_and_white")` → {"count": 38}
- 3. [user selects 4 from r2 → r3]
- 4. `make_collage(images="r3")` → {"count": 1}
- 5. `move_to_album(images="r4", album="Notes and Syllabi")` → {"count": 1, "album": "Notes and Syllabi", "created": false}
- turns: [[1, 2], [3, 4, 5]]
- query hints: step 1: documents/medium e.g. ['daily class schedule', 'doodles on scrap paper', 'highlighted text in notebook']

## ep_0032 · P030 · Callum (persona_02, uk) · collage_max 4 · effects cool, sepia · turns one_message
*Callum wants to try applying a cool filter to photos of his wife against crashing waves on rocks from Cornwall two weeks ago. After reviewing the filtered duplicates, he deletes the ones where the effect did not look good.*

- 1. `search_images(query="crashing waves on rocks", people=["wife"], location="Cornwall", date="two weeks ago")` → {"count": 17}
- 2. `apply_effect(images="r1", effect="cool")` → {"count": 17}
- 3. [user selects 9 from r2 → r3]
- 4. `delete_images(images="r3")` → {"count": 9}
- turns: [[1, 2], [3, 4]]
- query hints: step 1: scenes/medium e.g. ['rainbow over the hills', 'taxi cab in traffic', 'heavy rain falling']

## ep_0033 · P078 · Robert (persona_15, us) · collage_max 4 · effects cool, warm, black_and_white, sepia · turns one_message
*Robert wants to apply a warm filter to the three photos he has selected and combine them into a collage to share with his wife, Linda.*

- 0. [starts with 3 photos selected → r0]
- 1. `apply_effect(images="r0", effect="warm")` → {"count": 3}
- 2. `make_collage(images="r1")` → {"count": 1}
- turns: [[1, 2]]

## ep_0034 · P029 · Leila (persona_07, uae) · collage_max 4 · effects black_and_white, warm, sepia · turns mixed
*Leila wants to create a vintage-style collage from her trip to Salalah. She searches for photos taken in Salalah, gives them a sepia tone, and selects three favorites to assemble into a collage.*

- 1. `search_images(location="Salalah")` → {"count": 35}
- 2. `apply_effect(images="r1", effect="sepia")` → {"count": 35}
- 3. [user selects 3 from r2 → r3]
- 4. `make_collage(images="r3")` → {"count": 1}
- turns: [[1], [2], [3, 4]]

## ep_0035 · P038 · Amelie (persona_01, canada) · collage_max 9 · effects sepia, black_and_white, warm, cool · turns one_message
*Amelie wants to start a new album of Canada Day celebrations to share with Sanjay. She searches for photos of watching fireworks, narrows them down to last Canada Day, and saves her favorite shot to the new album.*

- 1. `search_images(query="watching fireworks")` → {"count": 18}
- 2. `search_images(query="watching fireworks", date="last Canada Day")` → {"count": 2} *(refines 1)*
- 3. [user selects 1 from r2 → r3]
- 4. `move_to_album(images="r3", album="Canada Day Celebrations")` → {"count": 1, "album": "Canada Day Celebrations", "created": true}
- turns: [[1], [2], [3, 4]]
- query hints: step 1: actions/short e.g. ['scuba diving', 'hitting pinata', 'eating breakfast']

## ep_0036 · P077 · Faridah (persona_19, india) · collage_max 9 · effects warm, black_and_white · turns mixed
*Faridah wants to apply a warm effect to her selected photos to give them a cozy festive glow, then organize the best edited shots into her Eid Celebrations album.*

- 0. [starts with 17 photos selected → r0]
- 1. `apply_effect(images="r0", effect="warm")` → {"count": 17}
- 2. [user selects 9 from r1 → r2]
- 3. `move_to_album(images="r2", album="Eid Celebrations")` → {"count": 9, "album": "Eid Celebrations", "created": false}
- turns: [[1], [2, 3]]

## ep_0037 · P034 · Brenda (persona_18, us) · collage_max 4 · effects black_and_white, cool, sepia, warm · turns one_message
*Brenda wants to add cool-toned photos of her brother to the family collection in the Lily 1st Birthday album to share with family members.*

- 1. `search_images(people=["brother"])` → {"count": 4}
- 2. `apply_effect(images="r1", effect="cool")` → {"count": 4}
- 3. `move_to_album(images="r2", album="Lily 1st Birthday")` → {"count": 4, "album": "Lily 1st Birthday", "created": false}
- turns: [[1, 2, 3]]

## ep_0038 · P015 · Aditya (persona_12, india) · collage_max 12 · effects sepia, black_and_white, warm, cool · turns one_message
*Aditya wants to make a collage of photos of Venkatesh and Sonu with tea cups from their trip to Munnar to share in their group chat.*

- 1. `search_images(query="tea cup", people=["Venkatesh", "Sonu"], location="Munnar")` → {"count": 10}
- 2. `make_collage(images="r1")` → {"count": 1}
- turns: [[1, 2]]
- query hints: step 1: objects/short e.g. ['bookshelf', 'laptop', 'area rug']

## ep_0039 · P041 · Faridah (persona_19, india) · collage_max 6 · effects warm, sepia, cool, black_and_white · turns mixed
*Faridah wants to look through photos of Rif'at to find his picture taken in Darjeeling and apply a warm effect to it.*

- 1. `search_images(people=["Rif'at"])` → {"count": 19}
- 2. `search_images(people=["Rif'at"], location="Darjeeling")` → {"count": 1} *(refines 1)*
- 3. `apply_effect(images="r2", effect="warm")` → {"count": 1}
- turns: [[1], [2, 3]]

## ep_0040 · P085 · Brenda (persona_18, us) · collage_max 6 · effects warm, sepia, black_and_white, cool · turns one_message
*Brenda wants black-and-white versions of her five selected photos to use as inspiration for a quilting project. She initially asks for a collage of them as well, but changes her mind in the same request and decides to delete the collage so she only keeps the individual black-and-white prints.*

- 0. [starts with 5 photos selected → r0]
- 1. `apply_effect(images="r0", effect="black_and_white")` → {"count": 5}
- 2. `make_collage(images="r1")` → {"count": 1}
- 3. `delete_images(images="r2")` → {"count": 1}
- turns: [[1, 2, 3]]

## ep_0041 · P032 · Aditya (persona_12, india) · collage_max 6 · effects cool, warm · turns one_message
*Aditya wants to make a warm-toned collage of photos featuring his friends and colleagues in graphic tees to display near his gaming setup.*

- 1. `search_images(query="graphic tees", people=["friend", "colleague"])` → {"count": 6}
- 2. `apply_effect(images="r1", effect="warm")` → {"count": 6}
- 3. `make_collage(images="r2")` → {"count": 1}
- turns: [[1, 2, 3]]
- query hints: step 1: clothing/short e.g. ['clown outfit', 'fairy wings', 'athletic shorts']

## ep_0042 · P001 · Joanne (persona_04, singapore) · collage_max 6 · effects sepia, black_and_white, cool · turns one_message
*Joanne is chatting with Siti about taking another vacation together and wants to check the exact dates of her previous trip to Bali.*

- 1. `ask_gallery(question="When was my trip to Bali?")` → {"answer": "Your trip to Bali was from October 5 to October 12, 2023.", "count": 4}
- turns: [[1]]

## ep_0043 · P043 · Nitika (persona_16, india) · collage_max 4 · effects cool, warm, sepia, black_and_white · turns mixed
*Nitika wants to create a collage using photos of Sai Krishna from their trip to Agra. After testing a sepia effect on the resulting collage, she dislikes the vintage look and decides to delete the edited version.*

- 1. `search_images(people=["Sai Krishna"], location="Agra")` → {"count": 21}
- 2. [user selects 2 from r1 → r2]
- 3. `make_collage(images="r2")` → {"count": 1}
- 4. `apply_effect(images="r3", effect="sepia")` → {"count": 1}
- 5. `delete_images(images="r4")` → {"count": 1}
- turns: [[1], [2, 3], [4], [5]]

## ep_0044 · P070 · Marcus (persona_03, us) · collage_max 9 · effects sepia, warm, cool, black_and_white · turns one_per_turn
*Marcus has selected 15 photos that turned out too warm and wants to apply a cool effect to balance their color tone before showing them to Elena.*

- 0. [starts with 15 photos selected → r0]
- 1. `apply_effect(images="r0", effect="cool")` → {"count": 15}
- turns: [[1]]

## ep_0045 · P055 · Amelie (persona_01, canada) · collage_max 12 · effects sepia, warm, black_and_white, cool · turns one_message
*Amelie wants to make a photo collage of Mei along snow-covered city streets in Reykjavik during Lunar New Year to add to her Anniversary 2021 album.*

- 1. `search_images(query="snow covered city street", people=["Mei"], location="Reykjavik")` → {"count": 39}
- 2. `search_images(query="snow covered city street", people=["Mei"], location="Reykjavik", date="during Lunar New Year")` → {"count": 27} *(refines 1)*
- 3. [user selects 10 from r2 → r3]
- 4. `make_collage(images="r3")` → {"count": 1}
- 5. `move_to_album(images="r4", album="Anniversary 2021")` → {"count": 1, "album": "Anniversary 2021", "created": false}
- turns: [[1], [2], [3, 4, 5]]
- query hints: step 1: scenes/medium e.g. ['cluttered kitchen island surface', 'fresh snow on ground', 'empty subway car']

## ep_0046 · P018 · Ishaan (persona_10, india) · collage_max 6 · effects cool, warm · turns one_per_turn
*Ishaan wants to organize his gallery by collecting all the photos he took in Lucknow last month into a dedicated new album.*

- 1. `search_images(location="Lucknow", date="last month")` → {"count": 19}
- 2. `move_to_album(images="r1", album="Lucknow Memories")` → {"count": 19, "album": "Lucknow Memories", "created": true}
- turns: [[1], [2]]

## ep_0047 · P027 · Robert (persona_15, us) · collage_max 4 · effects black_and_white, cool, warm, sepia · turns mixed
*Robert wants to make a black-and-white collage using photos of himself in Miami to share with his family.*

- 1. `search_images(people=["me"], location="Miami")` → {"count": 3}
- 2. `make_collage(images="r1")` → {"count": 1}
- 3. `apply_effect(images="r2", effect="black_and_white")` → {"count": 1}
- turns: [[1, 2, 3]]

## ep_0048 · P036 · Riya (persona_08, india) · collage_max 6 · effects warm, black_and_white, sepia, cool · turns one_per_turn
*Riya wants to delete blurry burst shots of Rohan and her brother with a dog playing with a tennis ball from last Diwali. She searches for their photos with the dog, narrows them down to last Diwali, and deletes the bad takes.*

- 1. `search_images(query="dog playing with a tennis ball", people=["Rohan", "brother"])` → {"count": 33}
- 2. `search_images(query="dog playing with a tennis ball", people=["Rohan", "brother"], date="last Diwali")` → {"count": 27} *(refines 1)*
- 3. [user selects 11 from r2 → r3]
- 4. `delete_images(images="r3")` → {"count": 11}
- turns: [[1], [2], [3, 4]]
- query hints: step 1: pets/long e.g. ['colorful fish swimming in an aquarium', 'dog catching a frisbee in air', 'guinea pig hiding in a tube']

## ep_0049 · P044 · Kevin (persona_20, us) · collage_max 9 · effects cool, black_and_white, warm, sepia · turns one_message
*Kevin wants to create a warm collage of Barnaby to add to the Oliver's 3rd Birthday album because Oliver adores their family dog.*

- 1. `search_images(people=["Barnaby"])` → {"count": 11}
- 2. [user selects 5 from r1 → r2]
- 3. `make_collage(images="r2")` → {"count": 1}
- 4. `apply_effect(images="r3", effect="warm")` → {"count": 1}
- 5. `move_to_album(images="r4", album="Oliver's 3rd Birthday")` → {"count": 1, "album": "Oliver's 3rd Birthday", "created": false}
- turns: [[1], [2, 3, 4, 5]]

## ep_0050 · P017 · Swathi (persona_11, india) · collage_max 4 · effects sepia, black_and_white, cool · turns mixed
*Swathi wants to give a warm, nostalgic look to her photos from Ooty taken during Eid, so she applies a sepia effect to all of them.*

- 1. `search_images(location="Ooty", date="during Eid")` → {"count": 20}
- 2. `apply_effect(images="r1", effect="sepia")` → {"count": 20}
- turns: [[1, 2]]
