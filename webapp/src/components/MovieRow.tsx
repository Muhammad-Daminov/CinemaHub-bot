import type { Movie } from "../types/movie";
import { MovieCard } from "./MovieCard";

interface Props {
  title: string;
  movies: Movie[];
  onSelect: (movie: Movie) => void;
}

export function MovieRow({ title, movies, onSelect }: Props) {
  if (movies.length === 0) return null;

  return (
    <section className="py-3">
      <div className="sprocket-divider mb-2" />
      <h2 className="mb-2 px-4 font-display text-base font-medium tracking-wide text-ink">{title}</h2>
      <div className="no-scrollbar flex gap-3 overflow-x-auto px-4 pb-1">
        {movies.map((movie) => (
          <MovieCard key={movie.id} movie={movie} onSelect={onSelect} />
        ))}
      </div>
    </section>
  );
}
