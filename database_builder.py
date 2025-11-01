import librosa
import os
import json
from yt_dlp import YoutubeDL
import psycopg2
from dotenv import load_dotenv
load_dotenv()
from config import Config
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import traceback
import tempfile
import warnings
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

"""
DATABASE BUILDER - Optional Tool for Advanced Users

This script is used to BUILD and EXPAND the tracks database by downloading audio metadata
from Spotify and SoundCloud playlists. The default database already includes 10,000 tracks,
so most users will NOT need to run this.

USE THIS IF YOU WANT TO:
- Add more tracks to your local database
- Build a custom music library for your bot
- Expand the default track collection

HOW TO USE:
1. Make sure you have Spotify API credentials in your .env (SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
2. Add your playlist URLs to the 'playlists' list below (Spotify or SoundCloud links)
3. Run: python database_builder.py
4. The script will download metadata and detect BPM for each track
5. New tracks will be added to your database

WARNING: This process can take a long time depending on playlist size!
Each track needs to be downloaded temporarily to analyze BPM, then deleted.

Example playlist URLs:
- Spotify: https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
- SoundCloud: https://soundcloud.com/username/sets/playlist-name
"""

# Add your Spotify or SoundCloud playlist links here
playlists = ["playlist-link-1",
             "playlist-link-2"]

class DatabaseBuilder:
    def __init__(self):
        self.count = 0
        print("[DEBUG] Connection string:", repr(Config.DATABASE_URL))
        if not Config.DATABASE_URL:
            raise ValueError("DATABASE_URL is empty or missing!")

        self.conn = psycopg2.connect(Config.DATABASE_URL)
        self.cursor = self.conn.cursor()
        # client_credentials_manager = SpotifyClientCredentials(
        #     client_id=Config.SPOTIFY_CLIENT_ID,
        #     client_secret=Config.SPOTIFY_CLIENT_SECRET
        # )

        # self.spotify = spotipy.Spotify(client_credentials_manager=client_credentials_manager)

    def insertTrack(self, data):
        insert_query = """
        INSERT INTO tracks (uploader, song, bpm, url, song_id, playlist_id, duration, platform)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        self.cursor.execute(insert_query, (
            data["uploader"],
            data["song"], 
            data["bpm"],
            data["url"],
            data["id"],
            data["playlist_id"],
            data["duration"],
            data["platform"]
        ))
        self.conn.commit()

    def downloadSoundcloudPlaylist(self, playlist: str):
        """Process SoundCloud playlist"""
        playlist_settings = {
            'extract_flat': True
        }
        playlist_downloader = YoutubeDL(playlist_settings)
        playlist_data = playlist_downloader.extract_info(playlist)

        for key, song in enumerate(playlist_data["entries"]):
            try:
                print(f"Currently in playlist: {playlist}")

                # Info available from playlist
                playlist_id = playlist_data["id"]
                song_url = song["url"]
                song_id = song["id"]
                
                # Digging deeper for more song info
                metadata_downloader = YoutubeDL({})
                song_data = metadata_downloader.extract_info(song_url, download=False)
                song_name = song_data["title"]
                duration = song_data["duration"]
                song_uploader = song_data["uploader"]

                search_settings = { 
                    'quiet': True,
                    'extract_flat': True
                }
                search_sc = YoutubeDL(search_settings)
                search_query = song_name + " " + song_uploader

                search_result = search_sc.extract_info(f"scsearch1:{search_query}", download=False)

                if search_result["entries"]:
                    found_song = search_result["entries"][0]
                    found_title = found_song["title"]
                    found_id = found_song["id"]
                    found_url = found_song["url"]

                    print(f"Soundcloud search successful!\nSong: {found_title} \nLink: {found_url}")

                    if found_id == song_id:
                        print(f"Match found for {found_title}!")
                    
                        # Settings for downloading the song
                        filename = song_name + song_uploader + ".mp3"
                        filename = self.sanitize_filename(filename)    
                        temp_dir = tempfile.mkdtemp()
                        temp_filename = os.path.join(temp_dir, filename) # put the filename inside temp_dir
                        
                        song_settings = {
                            'format': 'bestaudio[ext=mp3]/bestaudio',   # Force mp3, default otherwise
                            'outtmpl': temp_filename,                   # name of file
                            'audioquality': '128K'
                        }
                        song_downloader = YoutubeDL(song_settings)
                        try:
                            song_data = song_downloader.download([song_url])
                            
                            try:
                                song_bpm = self.detectBPM(temp_filename)
                            except Exception as e:
                                print(f"Error trying to detect BPM, skipping..")
                                continue

                            # Store both in playlist_db as one entry
                            song_data = {"uploader": song_uploader,
                                    "song": song_name,
                                    "bpm": song_bpm,
                                    "url": song_url,
                                    "id": song_id,
                                    "playlist_id": playlist_id,
                                    "duration": duration,
                                    "platform": "soundcloud"
                            }
                            try:
                                self.ensure_connection()
                                self.insertTrack(song_data)
                                self.count += 1
                                print(f"Success! Inserted song: {self.count}\n")
                            except psycopg2.IntegrityError:
                                print(f"Duplicate song {song_name}, skipping...")
                                self.conn.rollback()
                        except Exception as e:
                            print(f"Failed to process {song_name}: {e}")
                            print("Skipping track and continuing..\n")
                            continue
                    else:
                        print(f"Match not found for {found_title}.. Skipping")
                        continue
            except Exception as e:
                print(f"Unexpected error: {e}")

    def downloadSpotifyPlaylist(self, playlist: str):
        """Process and download Spotify playlists"""
        try:
            playlist_id = playlist.split("/playlist/")[1].split('?')[0]
        except IndexError:
            print(f"Invalid Spotify playlist URL: {playlist}")

        yt_search_settings = {
            'quiet': True,          # Don't download progress
            'extract_flat': True    # Don't download, just get metadata
        }
        yt_searcher = YoutubeDL(yt_search_settings)
        
        # Use Spotify API to get metadata
        songs_data = self.spotify.playlist_tracks(playlist_id)
        # print(json.dumps(data['items'], indent=2))
        for song in songs_data['items']:
            try:
                print(f"Currently in playlist: {playlist}")

                song_artist = song['track']['artists'][0]['name']
                song_name = song['track']['name']

                search_query = song_artist + " - " + song_name
                filename = song_name + song_artist + ".mp3"
                filename = self.sanitize_filename(filename)    
                temp_dir = tempfile.mkdtemp()
                temp_filename = os.path.join(temp_dir, filename) # put the filename inside temp_dir

                yt_download_settings = {
                    'format': 'bestaudio[ext=mp3]/bestaudio',   # Force mp3 format, default to original otherwise
                    'outtmpl': temp_filename,                   # Manually set filename
                    'extractaudio': True,                       # Extract audio from video if needed
                    'audioformat': 'mp3',                       # Convert to mp3 if not already
                    'audioquality': '128K'                      # Set audio quality
                }
                yt_dl = YoutubeDL(yt_download_settings)
                search_result = yt_searcher.extract_info(f"ytsearch1:{search_query}", download=False)
                
                if search_result["entries"]:
                    # info of get first search result (first video)
                    video_title = search_result["entries"][0]["title"]
                    video_id = search_result["entries"][0]["id"]
                    video_duration = search_result["entries"][0].get("duration", 0) # If duration metadata isnt available, default to 0
                    search_url = search_result["entries"][0]["url"]

                    if song_artist.lower() in video_title.lower() or song_name.lower() in video_title.lower():
                        print(search_query + " has been found!")
                        print(f"Here's its URL: {search_url}\n")
                        # download video's audio
                        try:
                            yt_dl.download([search_url])     

                            # analyze bpm
                            try:
                                song_bpm = self.detectBPM(temp_filename)
                            except Exception as e:
                                print(f"Error trying to detect BPM, skipping..")
                                continue

                            # create json list and put it into database
                            song_data = {"uploader": song_artist,
                                    "song": song_name,
                                    "bpm": song_bpm,
                                    "url": search_url,
                                    "id": video_id,
                                    "playlist_id": playlist_id,
                                    "duration": video_duration,
                                    "platform": "spotify"
                            }
                            try:
                                self.ensure_connection()
                                self.insertTrack(song_data)
                                self.count += 1
                                print(f"Success! Inserted song: {self.count}\n")
                            except psycopg2.IntegrityError:
                                print(f"Duplicate song {song_name}, skipping...")
                                self.conn.rollback()
                        except Exception as e:
                            print(f"Failed to process {search_query}: {e}")
                            print("Skipping track and continuing..\n")
                            continue
                    else:
                        print(search_query + " has not been found..\n")
                else:
                    print("Failed to search")
            except Exception as e:
                print(f"Unexpected error: {e}")
     
    def detectBPM(self, filename: str) -> int:
        try:
            if not os.path.exists(filename):
                print(f"Warning: File {filename} does not exist")
                return 120  # Default BPM
                
            waveform, sample_rate = librosa.load(filename)
            tempo, beats = librosa.beat.beat_track(y=waveform, sr=sample_rate, hop_length=512, start_bpm=120)
            bpm = int(tempo.item())

            if os.path.exists(filename):
                os.remove(filename)
                print(f"Deleted: {filename}")
            return bpm
        
        except Exception as e:
            print(f"Error in BPM detection: {e}")
            # Still try to clean up the file
            if os.path.exists(filename):
                os.remove(filename)
                print(f"Cleaned up file after error: {filename}")
            return 120  # Default BPM

    def detectLink(self, link: str) -> str:
        """Detect whether the link is a spotify or soundcloud playlist"""
        if "spotify" in link:
            return "spotify"
        else:
            return "soundcloud"

    def sanitize_filename(self, filename):
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        return filename[:200]  # Limit length too
        
    def ensure_connection(self):
        try:
            # Test the connection with a simple query
            self.cursor.execute("SELECT 1")
            self.conn.commit()
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            print("Database connection lost, reconnecting...")
            try:
                self.conn.close()
            except:
                pass
            
            # Reconnect
            print("[DEBUG] Connection string:", repr(Config.DATABASE_URL))
            if not Config.DATABASE_URL:
                raise ValueError("DATABASE_URL is empty or missing!")

            self.conn = psycopg2.connect(Config.DATABASE_URL)
            self.cursor = self.conn.cursor()
            print("Database reconnection successful!")

if __name__ == "__main__":
    try:
        builder = DatabaseBuilder()
        playlists = [
            "https://soundcloud.com/user-861307605/sets/nostalgia-ultra",
            "https://soundcloud.com/ari-ellingson/sets/mk-gee-1",
            "https://soundcloud.com/user-454354738/sets/kpldhprqix8x",
            "https://soundcloud.com/joel-jude-yepez/sets/toe-for-long-tomorrow",
            "https://soundcloud.com/user-751060177/sets/salvia-path",
            "https://soundcloud.com/ba-ed-krook/sets/some-typa-way?si=ef3aa158198c4047a0270b1cecde1906",
            "https://soundcloud.com/user-215570450/sets/mitski",
            "https://soundcloud.com/deceptive_expectations/sets/mitski-laurel-hell",
            "https://soundcloud.com/raian-cma/sets/radiohead"
        ]
        for playlist in playlists:
            link = builder.detectLink(playlist)
            if link == "spotify":
                builder.downloadSpotifyPlaylist(playlist=playlist)
            elif link == "soundcloud":
                builder.downloadSoundcloudPlaylist(playlist=playlist)
            else:
                print(f"Playlist not detected!")

        print(f"--------------------------------------")
        print(f"All playlists have been added to the database!")

    except KeyboardInterrupt:
        print(f"\nProcess interrupted by user. Exiting...")
    except Exception as e:
        print(f"An error occured: {e}")
        traceback.print_exc()