import { NextResponse } from 'next/server';
import { ObjectId } from 'mongodb';
import clientPromise from '@/lib/mongodb';
import { verify } from 'jsonwebtoken';
import { cookies } from 'next/headers';

export async function POST(
  request: Request,
  { params }: { params: { id: string } }
) {
  try {
    const cookieStore = cookies();
    const token = cookieStore.get('token');

    if (!token) {
      return NextResponse.json({ error: 'Not authenticated' }, { status: 401 });
    }

    let decoded;
    try {
      decoded = verify(token.value, process.env.JWT_SECRET!) as { userId: string };
    } catch (error) {
      return NextResponse.json({ error: 'Invalid token' }, { status: 401 });
    }

    const { songId } = await request.json();
    if (!songId) {
      return NextResponse.json({ error: 'Song ID is required' }, { status: 400 });
    }

    const client = await clientPromise;
    const db = client.db("mongomp");

    const result = await db.collection("playlists").updateOne(
      { _id: new ObjectId(params.id), user_id: new ObjectId(decoded.userId) },
      { $addToSet: { songs: new ObjectId(songId) } }
    );

    if (result.matchedCount === 0) {
      return NextResponse.json({ error: 'Playlist not found or unauthorized' }, { status: 404 });
    }

    return NextResponse.json({ message: 'Song added to playlist successfully' });
  } catch (e) {
    console.error(e);
    return NextResponse.json({ error: 'Failed to add song to playlist' }, { status: 500 });
  }
}
