import os
import shutil
import time
import stat
import gradio as gr
import modules.extras
import modules.ui
import json
import re
from modules.shared import opts, cmd_opts
from modules import shared, scripts, images
from modules.ui_common import plaintext_to_html
from modules import script_callbacks
from pathlib import Path
from typing import List, Tuple
from PIL.ExifTags import TAGS
from PIL.PngImagePlugin import PngImageFile, PngInfo


faverate_tab_name = "Favorites"
tabs_list = ["txt2img", "img2img",  "instruct-pix2pix", "txt2img-grids", "img2img-grids", "Extras", faverate_tab_name, "Others"] #txt2img-grids and img2img-grids added by HaylockGrant
num_of_imgs_per_page = 0
loads_files_num = 0
path_recorder_filename = os.path.join(scripts.basedir(), "path_recorder.txt")
image_ext_list = [".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"]
cur_ranking_value="0"
finfo_aes = {}
finfo_exif = {}
        
def reduplicative_file_move(src, dst):
    def same_name_file(basename, path):
        name, ext = os.path.splitext(basename)
        f_list = os.listdir(path)
        max_num = 0
        for f in f_list:
            if len(f) <= len(basename):
                continue
            f_ext = f[-len(ext):] if len(ext) > 0 else ""
            if f[:len(name)] == name and f_ext == ext:                
                if f[len(name)] == "(" and f[-len(ext)-1] == ")":
                    number = f[len(name)+1:-len(ext)-1]
                    if number.isdigit():
                        if int(number) > max_num:
                            max_num = int(number)
        return f"{name}({max_num + 1}){ext}"
    name = os.path.basename(src)
    save_name = os.path.join(dst, name)
    if not os.path.exists(save_name):
        shutil.move(src, dst)
    else:
        name = same_name_file(name, dst)
        shutil.move(src, os.path.join(dst, name))

def save_image(file_name):
    if file_name is not None and os.path.exists(file_name):
        reduplicative_file_move(file_name, opts.outdir_save)
        return "<div style='color:#999'>Moved to favorites</div>"
    else:
        return "<div style='color:#999'>Image not found (may have been already moved)</div>"

def create_ranked_file(filename, ranking):
    ranking_file = 'ranking.json'

    if not os.path.isfile(ranking_file):
        data = {}
        
    else:
        with open(ranking_file, 'r') as file:
            data = json.load(file)

    data[filename] = ranking

    with open(ranking_file, 'w') as file:
        json.dump(data, file)

def delete_image(delete_num, name, filenames, image_index, visible_num):
    if name == "":
        return filenames, delete_num
    else:
        delete_num = int(delete_num)
        visible_num = int(visible_num)
        image_index = int(image_index)
        index = list(filenames).index(name)
        i = 0
        new_file_list = []
        for name in filenames:
            if i >= index and i < index + delete_num:
                if os.path.exists(name):
                    if visible_num == image_index:
                        new_file_list.append(name)
                        i += 1
                        continue
                    if opts.images_delete_message:
                        print(f"Deleting file {name}")
                    os.remove(name)
                    visible_num -= 1
                    txt_file = os.path.splitext(name)[0] + ".txt"
                    if os.path.exists(txt_file):
                        os.remove(txt_file)
                else:
                    print(f"File does not exist {name}")
            else:
                new_file_list.append(name)
            i += 1
    return new_file_list, 1, visible_num

def traverse_all_files(curr_path, image_list) -> List[Tuple[str, os.stat_result]]:
    if curr_path == "":
        return image_list
    f_list = [(os.path.join(curr_path, entry.name), entry.stat()) for entry in os.scandir(curr_path)]
    for f_info in f_list:
        fname, fstat = f_info
        if os.path.splitext(fname)[1] in image_ext_list:
            image_list.append(f_info)
        elif stat.S_ISDIR(fstat.st_mode):
            image_list = traverse_all_files(fname, image_list)
    return image_list


def cache_aes(fileinfos):
    aes_cache_file = 'aes_scores.json'
    aes_cache = {}
    
    if os.path.isfile(aes_cache_file):
        with open(aes_cache_file, 'r') as file:
            aes_cache = json.load(file)
            
    for fi_info in fileinfos:
        if fi_info[0] in aes_cache:
            finfo_aes[fi_info[0]] = aes_cache[fi_info[0]]
        else:
            finfo_aes[fi_info[0]] = "0"
            aes_cache[fi_info[0]] = "0"
            try:
                image = PngImageFile(fi_info[0])
                allExif = modules.extras.run_pnginfo(image)[1]
                if allExif and allExif != "":
                    m = re.search("aesthetic_score: (\d+\.\d+)", allExif)
                    if m:
                        finfo_aes[fi_info[0]] = m.group(1)
                        aes_cache[fi_info[0]] = m.group(1)
                else:
                    try:
                        filename = os.path.splitext(fi_info[0])[0] + ".txt"
                        geninfo = ""
                        with open(filename) as f:
                            for line in f:
                                geninfo += line
                        finfo_exif[fi_info[0]] = geninfo
                        exif_cache[fi_info[0]] = geninfo
                    except Exception:
                        print(f"No exif for {fi_info[0]}")
            except SyntaxError:
                print(f"Non-PNG file in directory when doing AES check: {fi_info[0]}")

    with open(aes_cache_file, 'w') as file:
        json.dump(aes_cache, file)

def cache_exif(fileinfos):
    exif_cache_file = 'exif_data.json'
    exif_cache = {}
    if os.path.isfile(exif_cache_file):
        with open(exif_cache_file, 'r') as file:
            exif_cache = json.load(file)
            
    for fi_info in fileinfos:
        if fi_info[0] in exif_cache:
            #print(f"{fi_info[0]} found in EXIF cache!")
            finfo_exif[fi_info[0]] = exif_cache[fi_info[0]]
        else:
            #print(f"{fi_info[0]} NOT found in exif cache!")
            finfo_exif[fi_info[0]] = "0"
            try:
                image = PngImageFile(fi_info[0])
                allExif = modules.extras.run_pnginfo(image)[1]
                if allExif:
                    finfo_exif[fi_info[0]] = allExif
                    exif_cache[fi_info[0]] = allExif
                else:
                    try:
                        filename = os.path.splitext(fi_info[0])[0] + ".txt"
                        geninfo = ""
                        with open(filename) as f:
                            for line in f:
                                geninfo += line
                        finfo_exif[fi_info[0]] = geninfo
                        exif_cache[fi_info[0]] = geninfo
                    except Exception:
                        print(f"No EXIF in PNG or txt file fpr {fi_info[0]}")
                    #print(f"{fi_info[0]} exif added: {allExif}!")
            except SyntaxError:
                print(f"Non-PNG file in directory when doing EXIF check: {fi_info[0]}")

    with open(exif_cache_file, 'w') as file:
        json.dump(exif_cache, file)

def atof(text):
    try:
        retval = float(text)
    except ValueError:
        retval = text
    return retval

def natural_keys(text):
    '''
    alist.sort(key=natural_keys) sorts in human order
    http://nedbatchelder.com/blog/200712/human_sorting.html
    (See Toothy's implementation in the comments)
    float regex comes from https://stackoverflow.com/a/12643073/190597
    '''
    return [ atof(c) for c in re.split(r'[+-]?([0-9]+(?:[.][0-9]*)?|[.][0-9]+)', text) ]


def get_all_images(dir_name, sort_by, keyword, ranking_filter, aes_filter, desc, exif_keyword):
    fileinfos = traverse_all_files(dir_name, [])
    keyword = keyword.strip(" ")
    
    cache_aes(fileinfos)
    cache_exif(fileinfos)
    
    if len(keyword) != 0:
        fileinfos = [x for x in fileinfos if keyword.lower() in x[0].lower()]
        filenames = [finfo[0] for finfo in fileinfos]
    if len(exif_keyword) != 0:
        fileinfos = [x for x in fileinfos if exif_keyword.lower() in finfo_exif[x[0]].lower()]
        filenames = [finfo[0] for finfo in fileinfos]
    if len(aes_filter) != 0:
        fileinfos = [x for x in fileinfos if finfo_aes[x[0]] >= aes_filter]
        filenames = [finfo[0] for finfo in fileinfos]   
    if ranking_filter != "All":
        fileinfos = [x for x in fileinfos if get_ranking(x[0]) in ranking_filter]
        filenames = [finfo[0] for finfo in fileinfos]
    if sort_by == "date":
        if not desc:
            fileinfos = sorted(fileinfos, key=lambda x: -x[1].st_mtime)
        else:
            fileinfos = reversed(sorted(fileinfos, key=lambda x: -x[1].st_mtime))
        filenames = [finfo[0] for finfo in fileinfos]
    elif sort_by == "path name":
        if not desc:
            fileinfos = sorted(fileinfos)
        else:
            fileinfos = reversed(sorted(fileinfos))
        filenames = [finfo[0] for finfo in fileinfos]
    elif sort_by == "ranking":
        finfo_ranked = {}
        for fi_info in fileinfos:
            finfo_ranked[fi_info[0]] = get_ranking(fi_info[0])
        if not desc:
            fileinfos = dict(sorted(finfo_ranked.items(), key=lambda x: (x[1], x[0])))
        else:
            fileinfos = dict(reversed(sorted(finfo_ranked.items(), key=lambda x: (x[1], x[0]))))
        filenames = [finfo for finfo in fileinfos]
    elif sort_by == "aesthetic_score":
        fileinfo_aes = {}
        for finfo in fileinfos:
            fileinfo_aes[finfo[0]] = finfo_aes[finfo[0]]
        if desc:
            fileinfos = dict(reversed(sorted(fileinfo_aes.items(), key=lambda x: (x[1], x[0]))))
        else:
            fileinfos = dict(sorted(fileinfo_aes.items(), key=lambda x: (x[1], x[0])))
        filenames = [finfo for finfo in fileinfos]
    else:
        sort_values = {}
        exif_info = dict(finfo_exif)
        if exif_info:
            for k, v in exif_info.items():
                match = re.search(r'(?<='+ sort_by.lower() + ":" ').*?(?=(,|$))', v.lower())
                if match:
                    sort_values[k] = match.group()
                else:
                    sort_values[k] = "0"
            if desc:
                fileinfos = dict(reversed(sorted(fileinfos, key=lambda x: natural_keys(sort_values[x[0]]))))
            else:
                fileinfos = dict(sorted(fileinfos, key=lambda x: natural_keys(sort_values[x[0]])))
            filenames = [finfo for finfo in fileinfos]
        else:
            filenames = [finfo for finfo in fileinfos]
        
     
    
    return filenames

def get_image_page(img_path, page_index, filenames, keyword, sort_by, ranking_filter, aes_filter, desc, exif_keyword):
    if page_index == 1 or page_index == 0 or len(filenames) == 0:
        filenames = get_all_images(img_path, sort_by, keyword, ranking_filter, aes_filter, desc, exif_keyword)
    page_index = int(page_index)
    length = len(filenames)
    max_page_index = length // num_of_imgs_per_page + 1
    page_index = max_page_index if page_index == -1 else page_index
    page_index = 1 if page_index < 1 else page_index
    page_index = max_page_index if page_index > max_page_index else page_index
    idx_frm = (page_index - 1) * num_of_imgs_per_page
    image_list = filenames[idx_frm:idx_frm + num_of_imgs_per_page]
    
    visible_num = num_of_imgs_per_page if  idx_frm + num_of_imgs_per_page < length else length % num_of_imgs_per_page 
    visible_num = num_of_imgs_per_page if visible_num == 0 else visible_num

    load_info = "<div style='color:#999' align='center'>"
    load_info += f"{length} images in this directory, divided into {int((length + 1) // num_of_imgs_per_page  + 1)} pages"
    load_info += "</div>"
    return filenames, page_index, image_list,  "", "",  "", visible_num, load_info


def get_current_file(tabname_box, num, page_index, filenames):
    file = filenames[int(num) + int((page_index - 1) * num_of_imgs_per_page)]
    return file
    
def show_image_info(tabname_box, num, page_index, filenames):
    file = filenames[int(num) + int((page_index - 1) * num_of_imgs_per_page)]
    tm =   "<div style='color:#999' align='right'>" + time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(os.path.getmtime(file))) + "</div>"
    return file, tm, num, file, ""

def show_next_image_info(tabname_box, num, page_index, filenames, auto_next):
    file = filenames[int(num) + int((page_index - 1) * num_of_imgs_per_page)]
    tm =   "<div style='color:#999' align='right'>" + time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(os.path.getmtime(file))) + "</div>"
    if auto_next:
        num = int(num) + 1
    return file, tm, num, file, ""

def change_dir(img_dir, path_recorder, load_switch, img_path_history):
    warning = None
    try:
        if not cmd_opts.administrator:        
            head = os.path.realpath(".")
            real_path = os.path.realpath(img_dir)
            if len(real_path) < len(head) or real_path[:len(head)] != head:
                warning = f"You have not permission to visit {img_dir}. If you want visit all directories, add command line argument option '--administrator', <a style='color:#990' href='https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Command-Line-Arguments-and-Settings'>More detail here</a>"
    except:
        pass  
    if warning is None:
        try:
            if os.path.exists(img_dir):
                try:
                    f = os.listdir(img_dir)                
                except:
                    warning = f"'{img_dir} is not a directory"
            else:
                warning = "The directory does not exist"
        except:
            warning = "The format of the directory is incorrect"   

    if warning is None: 
        if img_dir not in path_recorder:
            path_recorder.append(img_dir)
        if os.path.exists(path_recorder_filename):
            os.remove(path_recorder_filename)
        if opts.images_record_paths:
            with open(path_recorder_filename, "a") as f:
                for x in path_recorder:
                    f.write(x + "\n")
        return "", gr.update(visible=True), gr.Dropdown.update(choices=path_recorder, value=img_dir), path_recorder, img_dir
    else:
        return warning, gr.update(visible=False), img_path_history, path_recorder, load_switch

def get_ranking(filename):
    ranking_file = 'ranking.json'
    ranking_value = "None"
    if os.path.isfile(ranking_file):
        with open(ranking_file, 'r') as file:
            data = json.load(file)
            if filename in data:
                ranking_value = data[filename]
                
    return ranking_value

def create_tab(tabname):
    custom_dir = False
    path_recorder = []
    
    try:
        if opts.outdir_ip2p_samples:
            ip2p_dirname = opts.outdir_ip2p_samples
    except AttributeError:
        ip2p_dirname = "outputs/ip2p-images"
    
    if tabname == "txt2img":
        dir_name = opts.outdir_txt2img_samples
    elif tabname == "img2img":
        dir_name = opts.outdir_img2img_samples
    elif tabname == "instruct-pix2pix":
        dir_name = ip2p_dirname
    elif tabname == "txt2img-grids":    #added by HaylockGrant to add a new tab for grid images
        dir_name = opts.outdir_txt2img_grids
    elif tabname == "img2img-grids":    #added by HaylockGrant to add a new tab for grid images
        dir_name = opts.outdir_img2img_grids
    elif tabname == "Extras":
        dir_name = opts.outdir_extras_samples
    elif tabname == faverate_tab_name:
        dir_name = opts.outdir_save
    else:
        custom_dir = True
        dir_name = None        
        if os.path.exists(path_recorder_filename):
            with open(path_recorder_filename) as f:
                path = f.readline().rstrip("\n")
                while len(path) > 0:
                    path_recorder.append(path)
                    path = f.readline().rstrip("\n")

    if not custom_dir:
        dir_name = str(Path(dir_name))
        if not os.path.exists(dir_name):
            os.makedirs(dir_name)

    with gr.Row(visible= custom_dir): 
        img_path = gr.Textbox(dir_name, label="Images directory", placeholder="Input images directory", interactive=custom_dir)  
        img_path_history = gr.Dropdown(path_recorder)
        path_recorder = gr.State(path_recorder)
 
    with gr.Row(visible= not custom_dir, elem_id=tabname + "_images_history") as main_panel:
        with gr.Column():  
            with gr.Row():    
                with gr.Column(scale=2):    
                    with gr.Row():       
                        first_page = gr.Button('First Page')
                        prev_page = gr.Button('Prev Page')
                        page_index = gr.Number(value=1, label="Page Index")
                        next_page = gr.Button('Next Page')
                        end_page = gr.Button('End Page') 
                    with gr.Column(scale=10):                            
                        ranking = gr.Radio(value="None", choices=["1", "2", "3", "4", "5", "None"], label="ranking", interactive="true")
                        auto_next = gr.Checkbox(label="Next Image After Ranking (To be implemented)", interactive="false")
                    history_gallery = gr.Gallery(show_label=False, elem_id=tabname + "_images_history_gallery").style(grid=opts.images_history_page_columns)
                    with gr.Row() as delete_panel:
                        with gr.Column(scale=1):
                            delete_num = gr.Number(value=1, interactive=True, label="delete next")
                        with gr.Column(scale=3):
                            delete = gr.Button('Delete', elem_id=tabname + "_images_history_del_button")
                
                with gr.Column(scale=1): 
                    with gr.Row(scale=0.5):
                            sort_by = gr.Dropdown(value="date", choices=["path name", "date", "aesthetic_score", "cfg scale", "steps", "seed", "sampler", "size", "model", "model hash", "ranking"], label="sort by")
                            desc = gr.Checkbox(label="Descending")
                    with gr.Row():
                        keyword = gr.Textbox(value="", label="filename keyword")
                        exif_keyword = gr.Textbox(value="", label="exif keyword")
                        
                    with gr.Column():
                        ranking_filter = gr.Radio(value="All", choices=["All", "1", "2", "3", "4", "5", "None"], label="ranking filter", interactive="true")
                        aes_filter = gr.Textbox(value="", label="minimum aesthetic_score")
                    with gr.Row():
                        with gr.Column():
                            img_file_info = gr.Textbox(label="Generate Info", interactive=False, lines=6)
                            img_file_name = gr.Textbox(value="", label="File Name", interactive=False)
                            img_file_time= gr.HTML()
                    with gr.Row(elem_id=tabname + "_images_history_button_panel") as button_panel:
                        if tabname != faverate_tab_name:
                            save_btn = gr.Button('Move to favorites')
                        try:
                            send_to_buttons = modules.generation_parameters_copypaste.create_buttons(["txt2img", "img2img", "inpaint", "extras"])
                        except:
                            pass
                    with gr.Row():
                        collected_warning = gr.HTML()
                    
                            
                    # hiden items
                    with gr.Row(visible=False): 
                        renew_page = gr.Button("Renew Page", elem_id=tabname + "_images_history_renew_page")
                        visible_img_num = gr.Number()                     
                        tabname_box = gr.Textbox(tabname)
                        image_index = gr.Textbox(value=-1)
                        set_index = gr.Button('set_index', elem_id=tabname + "_images_history_set_index")
                        filenames = gr.State([])
                        all_images_list = gr.State()
                        hidden = gr.Image(type="pil")
                        info1 = gr.Textbox()
                        info2 = gr.Textbox()
                        load_switch = gr.Textbox(value="load_switch", label="load_switch")
                        turn_page_switch = gr.Number(value=1, label="turn_page_switch")
    with gr.Row():                 
        warning_box = gr.HTML() 

    change_dir_outputs = [warning_box, main_panel, img_path_history, path_recorder, load_switch]
    img_path.submit(change_dir, inputs=[img_path, path_recorder, load_switch, img_path_history], outputs=change_dir_outputs)
    img_path_history.change(change_dir, inputs=[img_path_history, path_recorder, load_switch, img_path_history], outputs=change_dir_outputs)
    img_path_history.change(lambda x:x, inputs=[img_path_history], outputs=[img_path])

    
    
    #delete
    delete.click(delete_image, inputs=[delete_num, img_file_name, filenames, image_index, visible_img_num], outputs=[filenames, delete_num, visible_img_num])
    delete.click(fn=None, _js="images_history_delete", inputs=[delete_num, tabname_box, image_index], outputs=None) 
    if tabname != faverate_tab_name: 
        save_btn.click(save_image, inputs=[img_file_name], outputs=[collected_warning])     

    #turn page
    first_page.click(lambda s:(1, -s) , inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])
    next_page.click(lambda p, s: (p + 1, -s), inputs=[page_index, turn_page_switch], outputs=[page_index, turn_page_switch])
    prev_page.click(lambda p, s: (p - 1, -s), inputs=[page_index, turn_page_switch], outputs=[page_index, turn_page_switch])
    end_page.click(lambda s: (-1, -s), inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])    
    load_switch.change(lambda s:(1, -s), inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])
    keyword.submit(lambda s:(1, -s), inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])
    exif_keyword.submit(lambda s:(1, -s), inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])
    aes_filter.submit(lambda s:(1, -s), inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])
    sort_by.change(lambda s:(1, -s), inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])
    ranking_filter.change(lambda s:(1, -s), inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])
    page_index.submit(lambda s: -s, inputs=[turn_page_switch], outputs=[turn_page_switch])
    renew_page.click(lambda s: -s, inputs=[turn_page_switch], outputs=[turn_page_switch])
    desc.change(lambda s:(1, -s), inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])
        
    turn_page_switch.change(
        fn=get_image_page, 
        inputs=[img_path, page_index, filenames, keyword, sort_by, ranking_filter, aes_filter, desc, exif_keyword], 
        outputs=[filenames, page_index, history_gallery, img_file_name, img_file_time, img_file_info, visible_img_num, warning_box]
    )
    turn_page_switch.change(fn=None, inputs=[tabname_box], outputs=None, _js="images_history_turnpage")
    turn_page_switch.change(fn=lambda:(gr.update(visible=False), gr.update(visible=False)), inputs=None, outputs=[delete_panel, button_panel])

    # other funcitons
    
    set_index.click(show_image_info, _js="images_history_get_current_img", inputs=[tabname_box, image_index, page_index, filenames], outputs=[img_file_name, img_file_time, image_index, hidden])
    set_index.click(fn=lambda:(gr.update(visible=True), gr.update(visible=True)), inputs=None, outputs=[delete_panel, button_panel])
    img_file_name.change(fn=lambda : "", inputs=None, outputs=[collected_warning])
    img_file_name.change(get_ranking, inputs=img_file_name, outputs=ranking)

   
    hidden.change(fn=run_pnginfo, inputs=[hidden, img_path, img_file_name], outputs=[info1, img_file_info, info2])
    
    #ranking
    ranking.change(create_ranked_file, inputs=[img_file_name, ranking])
    #ranking.change(show_next_image_info, _js="images_history_get_current_img", inputs=[tabname_box, image_index, page_index, auto_next], outputs=[img_file_name, img_file_time, image_index, hidden])
    
    
    try:
        modules.generation_parameters_copypaste.bind_buttons(send_to_buttons, img_file_name, img_file_info)
    except:
        pass
    
def run_pnginfo(image, image_path, image_file_name):
    if image is None:
        return '', '', ''
    geninfo, items = images.read_info_from_image(image)
    items = {**{'parameters': geninfo}, **items}

    info = ''
    for key, text in items.items():
        info += f"""
<div>
<p><b>{plaintext_to_html(str(key))}</b></p>
<p>{plaintext_to_html(str(text))}</p>
</div>
""".strip()+"\n"
    
    if geninfo is None:
        try:
            filename = os.path.splitext(image_file_name)[0] + ".txt"
            geninfo = ""
            with open(filename) as f:
                for line in f:
                    geninfo += line
        except Exception:
            print(f"No EXIF in PNG or txt file")
    return '', geninfo, info


def on_ui_tabs():
    global num_of_imgs_per_page
    global loads_files_num
    num_of_imgs_per_page = int(opts.images_history_page_columns * opts.images_history_page_rows)
    loads_files_num = int(opts.images_history_pages_perload * num_of_imgs_per_page)
    with gr.Blocks(analytics_enabled=False) as images_history:
        with gr.Tabs(elem_id="images_history_tab)") as tabs:
            for tab in tabs_list:
                with gr.Tab(tab):
                    with gr.Blocks(analytics_enabled=False) :
                        create_tab(tab)
        gr.Checkbox(opts.images_history_preload, elem_id="images_history_preload", visible=False)         
        gr.Textbox(",".join(tabs_list), elem_id="images_history_tabnames_list", visible=False) 
    return (images_history , "Image Browser", "images_history"),

def on_ui_settings():
    section = ('images-history', "Images Browser")
    shared.opts.add_option("images_history_preload", shared.OptionInfo(False, "Preload images at startup", section=section))
    shared.opts.add_option("images_record_paths", shared.OptionInfo(True, "Record accessable images directories", section=section))
    shared.opts.add_option("images_delete_message", shared.OptionInfo(True, "Print image deletion messages to the console", section=section))
    shared.opts.add_option("images_history_page_columns", shared.OptionInfo(6, "Number of columns on the page", section=section))
    shared.opts.add_option("images_history_page_rows", shared.OptionInfo(6, "Number of rows on the page", section=section))
    shared.opts.add_option("images_history_pages_perload", shared.OptionInfo(20, "Minimum number of pages per load", section=section))


script_callbacks.on_ui_settings(on_ui_settings)
script_callbacks.on_ui_tabs(on_ui_tabs)

#TODO:
#recycle bin
#move by arrow key
#generate info in txt
